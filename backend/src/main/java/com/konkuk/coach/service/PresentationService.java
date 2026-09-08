package com.konkuk.coach.service;

import com.konkuk.coach.domain.Presentation;
import com.konkuk.coach.domain.PresentationStatus;
import com.konkuk.coach.dto.request.AnalysisCallbackRequest;
import com.konkuk.coach.dto.request.PresentationCreateRequest;
import com.konkuk.coach.dto.request.PresentationSubmitRequest;
import com.konkuk.coach.dto.request.SqsJobMessage;
import com.konkuk.coach.dto.response.PresentationCreateResponse;
import com.konkuk.coach.dto.response.PresentationReportResponse;
import com.konkuk.coach.dto.response.PresentationSubmitResponse;
import com.konkuk.coach.exception.BusinessException;
import com.konkuk.coach.exception.PresentationErrorCode;
import com.konkuk.coach.repository.PresentationRepository;
import lombok.RequiredArgsConstructor;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import software.amazon.awssdk.awscore.presigner.PresignRequest;
import software.amazon.awssdk.services.s3.S3Client;
import software.amazon.awssdk.services.s3.model.DeleteObjectRequest;
import software.amazon.awssdk.services.s3.model.PutObjectRequest;
import software.amazon.awssdk.services.s3.presigner.S3Presigner;
import software.amazon.awssdk.services.s3.presigner.model.PresignedPutObjectRequest;
import software.amazon.awssdk.services.s3.presigner.model.PutObjectPresignRequest;
import software.amazon.awssdk.services.sqs.SqsClient;
import software.amazon.awssdk.services.sqs.model.SendMessageRequest;
import tools.jackson.databind.ObjectMapper;

import java.security.SecureRandom;
import java.time.Duration;
import java.time.LocalDateTime;
import java.util.Base64;

@Service
@RequiredArgsConstructor
public class PresentationService {

    private final PresentationRepository presentationRepository;
    private final S3Client s3Client;
    private final S3Presigner s3Presigner;
    private final SqsClient sqsClient;
    private final ObjectMapper objectMapper;

    @Value("${app.s3.bucket}") private String bucket;
    @Value("${app.sqs.queue-url}") private String queueUrl;
    @Value("${app.front.base-url}") private String frontBaseUrl;
    @Value("${app.backend.base-url}") private String backendBaseUrl;

    private static final long AUDIO_LIMIT_MS = 600_000;

    @Transactional
    public PresentationCreateResponse create(PresentationCreateRequest request) {
        Presentation presentation = new Presentation();

        presentation.setTitle(request.title());
        presentation.setScript(request.script());

        presentation.setSlideS3Key("PENDING");   // id 모르니 임시값
        presentation.setAudioS3Key("PENDING");
        presentation.setResultToken(generateResultToken());
        presentationRepository.save(presentation);

        String slideKey = "presentations/" + presentation.getId() + "/slides.pptx";
        String audioKey = "presentations/" + presentation.getId() + "/audio.webm";
        presentation.setSlideS3Key(slideKey);
        presentation.setAudioS3Key(audioKey);
        presentationRepository.save(presentation);

        String slideUploadUrl = presignPutUrl(slideKey);
        String audioUploadUrl = presignPutUrl(audioKey);
        String resultUrl = frontBaseUrl + "/r/" + presentation.getId() + "?token=" + presentation.getResultToken();

        return new PresentationCreateResponse(
                presentation.getId(), slideUploadUrl, slideKey, audioUploadUrl, audioKey,
                presentation.getResultToken(), resultUrl, presentation.getExpiresAt()
        );
    }

    @Transactional
    public PresentationSubmitResponse submit(Long id, PresentationSubmitRequest request) {
        Presentation presentation = presentationRepository.findById(id)
                .orElseThrow(() -> new BusinessException(PresentationErrorCode.PRESENTATION_ID_NOT_FOUND));

        if (request.audioDurationMs() > AUDIO_LIMIT_MS) {
            throw new BusinessException(PresentationErrorCode.AUDIO_DURATION_EXCEEDED,
                    "오디오 길이(" + request.audioDurationMs() + "ms)가 10분 제한을 초과했습니다.");
        }

        presentation.setAudioDurationMs(request.audioDurationMs());

        SqsJobMessage job = new SqsJobMessage(
                presentation.getId(),
                presentation.getSlideS3Key(),
                presentation.getAudioS3Key(),
                presentation.getScript(),
                backendBaseUrl + "/api/internal/analysis-results"
        );
        sendJobMessage(job);

        presentation.setStatus(PresentationStatus.PROCESSING);
        presentationRepository.save(presentation);

        return new PresentationSubmitResponse(presentation.getId(), presentation.getStatus().name());
    }

    @Transactional
    public void applyAnalysisResult(AnalysisCallbackRequest request) {
        Presentation presentation = presentationRepository.findById(request.presentationId())
                .orElseThrow(()-> new BusinessException(PresentationErrorCode.PRESENTATION_ID_NOT_FOUND));

        if ("DONE".equals(request.status())) {
            presentation.setStatus(PresentationStatus.DONE);
            presentation.setTranscriptJson(request.transcript() != null ? request.transcript().toString() : null);
            presentation.setReportJson(request.report() != null ? request.report().toString() : null);
        } else {
            presentation.setStatus(PresentationStatus.FAILED);
            if (request.error() != null) {
                presentation.setErrorCode(request.error().code());
                presentation.setErrorMessage(request.error().message());
            } else {
                presentation.setErrorCode("UNKNOWN_ERROR");
                presentation.setErrorMessage("ai가 실패 사유를 전달하지 않았습니다.");
            }
        }
        presentationRepository.save(presentation);

        s3Client.deleteObject(DeleteObjectRequest.builder()
                        .bucket(bucket)
                        .key(presentation.getAudioS3Key())
                .build());
    }

    public PresentationReportResponse report(Long id, String secret) {
        Presentation presentation = presentationRepository.findById(id)
                .orElseThrow(()-> new BusinessException(PresentationErrorCode.PRESENTATION_ID_NOT_FOUND));

        if (!presentation.getResultToken().equals(secret)) {
            throw new BusinessException(PresentationErrorCode.PRESENTATION_NOT_FOUND);
        }

        if (presentation.getExpiresAt().isBefore(LocalDateTime.now())) {
            throw new BusinessException(PresentationErrorCode.PRESENTATION_EXPIRED);
        }

        return switch (presentation.getStatus()) {
            case DONE -> PresentationReportResponse.done(presentation.getId(), presentation.getReportJson());
            case FAILED -> PresentationReportResponse.failed(presentation.getId(), presentation.getErrorCode(), presentation.getErrorMessage());
            case PENDING, PROCESSING -> PresentationReportResponse.processing(presentation.getId());
        };
    }

    private String presignPutUrl(String key) {
        PutObjectRequest putObjectRequest = PutObjectRequest.builder()
                .bucket(bucket)
                .key(key)
                .build();
        PutObjectPresignRequest presignRequest = PutObjectPresignRequest.builder()
                .signatureDuration(Duration.ofMinutes(30))
                .putObjectRequest(putObjectRequest)
                .build();
        PresignedPutObjectRequest presigned = s3Presigner.presignPutObject(presignRequest);
        return presigned.url().toString();
    }

    private void sendJobMessage(SqsJobMessage job) {
        try {
            String body = objectMapper.writeValueAsString(job);
            sqsClient.sendMessage(SendMessageRequest.builder()
                    .queueUrl(queueUrl)
                    .messageBody(body)
                    .build());
        } catch (Exception e) {
            throw new RuntimeException("SQS 메세지 발행 실패", e);
        }
    }

    private String generateResultToken() {
        byte[] bytes = new byte[32];
        new SecureRandom().nextBytes(bytes);
        return Base64.getUrlEncoder().withoutPadding().encodeToString(bytes);
    }
}
