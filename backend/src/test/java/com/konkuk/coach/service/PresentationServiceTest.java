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
import com.konkuk.coach.exception.ErrorBody;
import com.konkuk.coach.exception.PresentationErrorCode;
import com.konkuk.coach.repository.PresentationRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.test.util.ReflectionTestUtils;
import software.amazon.awssdk.services.s3.S3Client;
import software.amazon.awssdk.services.s3.model.DeleteObjectRequest;
import software.amazon.awssdk.services.s3.presigner.S3Presigner;
import software.amazon.awssdk.services.s3.presigner.model.PresignedPutObjectRequest;
import software.amazon.awssdk.services.s3.presigner.model.PutObjectPresignRequest;
import software.amazon.awssdk.services.sqs.SqsClient;
import software.amazon.awssdk.services.sqs.model.SendMessageRequest;
import tools.jackson.databind.ObjectMapper;

import java.net.URI;
import java.time.LocalDateTime;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class PresentationServiceTest {

    @Mock private PresentationRepository presentationRepository;
    @Mock private S3Client s3Client;
    @Mock private S3Presigner s3Presigner;
    @Mock private SqsClient sqsClient;
    @Mock private ObjectMapper objectMapper;

    private PresentationService presentationService;

    @BeforeEach
    void setUp() {
        presentationService = new PresentationService(
                presentationRepository, s3Client, s3Presigner, sqsClient, objectMapper);
        ReflectionTestUtils.setField(presentationService, "bucket", "test-bucket");
        ReflectionTestUtils.setField(presentationService, "queueUrl", "https://sqs.test/queue");
        ReflectionTestUtils.setField(presentationService, "frontBaseUrl", "https://front.test");
        ReflectionTestUtils.setField(presentationService, "backendBaseUrl", "https://backend.test");
    }

    @Test
    @DisplayName("create: 두 번 저장하고 S3 키와 토큰을 채운다")
    void createFillsS3KeysAndToken() throws Exception {
        when(presentationRepository.save(any(Presentation.class))).thenAnswer(invocation -> {
            Presentation presentation = invocation.getArgument(0);
            if (presentation.getId() == null) {
                presentation.setId(1L);
            }
            return presentation;
        });

        PresignedPutObjectRequest presigned = mock(PresignedPutObjectRequest.class);
        when(presigned.url()).thenReturn(URI.create("https://s3.test/presigned").toURL());
        when(s3Presigner.presignPutObject(any(PutObjectPresignRequest.class))).thenReturn(presigned);

        PresentationCreateRequest request = new PresentationCreateRequest("제목", "스크립트");

        PresentationCreateResponse response = presentationService.create(request);

        verify(presentationRepository, times(2)).save(any(Presentation.class));
        assertThat(response.presentationId()).isEqualTo(1L);
        assertThat(response.slideS3Key()).isEqualTo("presentations/1/slides.pptx");
        assertThat(response.audioS3Key()).isEqualTo("presentations/1/audio.webm");
        assertThat(response.resultToken()).hasSize(43);
        assertThat(response.resultUrl()).contains(response.resultToken());
    }

    @Test
    @DisplayName("submit: 정상이면 PROCESSING으로 바뀌고 SQS로 전송된다")
    void submitSuccessSendsSqsMessage() {
        Presentation presentation = new Presentation();
        presentation.setId(1L);
        presentation.setSlideS3Key("presentations/1/slides.pptx");
        presentation.setAudioS3Key("presentations/1/audio.webm");
        presentation.setScript("스크립트");

        when(presentationRepository.findById(1L)).thenReturn(Optional.of(presentation));
        when(presentationRepository.save(any(Presentation.class))).thenReturn(presentation);
        when(objectMapper.writeValueAsString(any(SqsJobMessage.class))).thenReturn("{}");

        PresentationSubmitResponse response = presentationService.submit(1L, new PresentationSubmitRequest(30_000));

        assertThat(response.status()).isEqualTo("PROCESSING");
        assertThat(presentation.getStatus()).isEqualTo(PresentationStatus.PROCESSING);
        verify(sqsClient).sendMessage(any(SendMessageRequest.class));
    }

    @Test
    @DisplayName("submit: 존재하지 않는 id면 예외")
    void submitNotFoundThrowsException() {
        when(presentationRepository.findById(999L)).thenReturn(Optional.empty());

        BusinessException e = assertThrows(BusinessException.class,
                () -> presentationService.submit(999L, new PresentationSubmitRequest(1000)));

        assertThat(e.getErrorCode()).isEqualTo(PresentationErrorCode.PRESENTATION_ID_NOT_FOUND);
    }

    @Test
    @DisplayName("submit: 오디오 길이가 10분 초과면 예외")
    void submitAudioDurationExceededThrowsException() {
        Presentation presentation = new Presentation();
        presentation.setId(1L);
        when(presentationRepository.findById(1L)).thenReturn(Optional.of(presentation));

        BusinessException e = assertThrows(BusinessException.class,
                () -> presentationService.submit(1L, new PresentationSubmitRequest(600_001)));

        assertThat(e.getErrorCode()).isEqualTo(PresentationErrorCode.AUDIO_DURATION_EXCEEDED);
        verify(sqsClient, never()).sendMessage(any(SendMessageRequest.class));
    }

    @Test
    @DisplayName("applyAnalysisResult: DONE이면 상태와 report를 저장한다")
    void applyAnalysisResultDoneSavesReport() {
        Presentation presentation = new Presentation();
        presentation.setId(1L);
        presentation.setAudioS3Key("presentations/1/audio.webm");
        when(presentationRepository.findById(1L)).thenReturn(Optional.of(presentation));
        when(presentationRepository.save(any(Presentation.class))).thenReturn(presentation);

        AnalysisCallbackRequest request = new AnalysisCallbackRequest(1L, "DONE", null, null, null);

        presentationService.applyAnalysisResult(request);

        assertThat(presentation.getStatus()).isEqualTo(PresentationStatus.DONE);
        verify(s3Client).deleteObject(any(DeleteObjectRequest.class));
    }

    @Test
    @DisplayName("applyAnalysisResult: FAILED이고 error가 없으면 UNKNOWN_ERROR로 채운다")
    void applyAnalysisResultFailedWithoutErrorSetsUnknown() {
        Presentation presentation = new Presentation();
        presentation.setId(1L);
        presentation.setAudioS3Key("presentations/1/audio.webm");
        when(presentationRepository.findById(1L)).thenReturn(Optional.of(presentation));
        when(presentationRepository.save(any(Presentation.class))).thenReturn(presentation);

        AnalysisCallbackRequest request = new AnalysisCallbackRequest(1L, "FAILED", null, null, null);

        presentationService.applyAnalysisResult(request);

        assertThat(presentation.getStatus()).isEqualTo(PresentationStatus.FAILED);
        assertThat(presentation.getErrorCode()).isEqualTo("UNKNOWN_ERROR");
    }

    @Test
    @DisplayName("applyAnalysisResult: FAILED이고 error가 있으면 그대로 저장한다")
    void applyAnalysisResultFailedWithErrorSavesAsIs() {
        Presentation presentation = new Presentation();
        presentation.setId(1L);
        presentation.setAudioS3Key("presentations/1/audio.webm");
        when(presentationRepository.findById(1L)).thenReturn(Optional.of(presentation));
        when(presentationRepository.save(any(Presentation.class))).thenReturn(presentation);

        AnalysisCallbackRequest request = new AnalysisCallbackRequest(
                1L, "FAILED", null, null, new ErrorBody("STT_FAILED", "음성 인식 실패"));

        presentationService.applyAnalysisResult(request);

        assertThat(presentation.getErrorCode()).isEqualTo("STT_FAILED");
        assertThat(presentation.getErrorMessage()).isEqualTo("음성 인식 실패");
    }

    @Test
    @DisplayName("report: DONE이면 reportJson을 반환한다")
    void reportDoneReturnsReportJson() {
        Presentation presentation = new Presentation();
        presentation.setId(1L);
        presentation.setStatus(PresentationStatus.DONE);
        presentation.setResultToken("token123");
        presentation.setExpiresAt(LocalDateTime.now().plusDays(1));
        presentation.setReportJson("{\"score\":90}");
        when(presentationRepository.findById(1L)).thenReturn(Optional.of(presentation));

        PresentationReportResponse response = presentationService.report(1L, "token123");

        assertThat(response.status()).isEqualTo("DONE");
        assertThat(response.report()).isEqualTo("{\"score\":90}");
    }

    @Test
    @DisplayName("report: FAILED면 error를 반환한다")
    void reportFailedReturnsError() {
        Presentation presentation = new Presentation();
        presentation.setId(1L);
        presentation.setStatus(PresentationStatus.FAILED);
        presentation.setResultToken("token123");
        presentation.setExpiresAt(LocalDateTime.now().plusDays(1));
        presentation.setErrorCode("STT_FAILED");
        presentation.setErrorMessage("음성 인식 실패");
        when(presentationRepository.findById(1L)).thenReturn(Optional.of(presentation));

        PresentationReportResponse response = presentationService.report(1L, "token123");

        assertThat(response.status()).isEqualTo("FAILED");
        assertThat(response.error().code()).isEqualTo("STT_FAILED");
    }

    @Test
    @DisplayName("report: PROCESSING이면 report가 null이다")
    void reportProcessingReturnsNullReport() {
        Presentation presentation = new Presentation();
        presentation.setId(1L);
        presentation.setStatus(PresentationStatus.PROCESSING);
        presentation.setResultToken("token123");
        presentation.setExpiresAt(LocalDateTime.now().plusDays(1));
        when(presentationRepository.findById(1L)).thenReturn(Optional.of(presentation));

        PresentationReportResponse response = presentationService.report(1L, "token123");

        assertThat(response.status()).isEqualTo("PROCESSING");
        assertThat(response.report()).isNull();
    }

    @Test
    @DisplayName("report: 토큰이 다르면 예외")
    void reportWrongTokenThrowsException() {
        Presentation presentation = new Presentation();
        presentation.setId(1L);
        presentation.setResultToken("token123");
        presentation.setExpiresAt(LocalDateTime.now().plusDays(1));
        when(presentationRepository.findById(1L)).thenReturn(Optional.of(presentation));

        BusinessException e = assertThrows(BusinessException.class,
                () -> presentationService.report(1L, "wrong-token"));

        assertThat(e.getErrorCode()).isEqualTo(PresentationErrorCode.PRESENTATION_NOT_FOUND);
    }

    @Test
    @DisplayName("report: 존재하지 않는 id면 예외")
    void reportNotFoundThrowsException() {
        when(presentationRepository.findById(999L)).thenReturn(Optional.empty());

        BusinessException e = assertThrows(BusinessException.class,
                () -> presentationService.report(999L, "token123"));

        assertThat(e.getErrorCode()).isEqualTo(PresentationErrorCode.PRESENTATION_ID_NOT_FOUND);
    }

    @Test
    @DisplayName("report: 만료됐으면 예외")
    void reportExpiredThrowsException() {
        Presentation presentation = new Presentation();
        presentation.setId(1L);
        presentation.setResultToken("token123");
        presentation.setExpiresAt(LocalDateTime.now().minusDays(1));
        when(presentationRepository.findById(1L)).thenReturn(Optional.of(presentation));

        BusinessException e = assertThrows(BusinessException.class,
                () -> presentationService.report(1L, "token123"));

        assertThat(e.getErrorCode()).isEqualTo(PresentationErrorCode.PRESENTATION_EXPIRED);
    }
}
