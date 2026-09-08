package com.konkuk.coach.scheduler;

import com.konkuk.coach.domain.Presentation;
import com.konkuk.coach.repository.PresentationRepository;
import lombok.RequiredArgsConstructor;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;
import software.amazon.awssdk.services.s3.S3Client;
import software.amazon.awssdk.services.s3.model.DeleteObjectRequest;

import java.time.LocalDateTime;
import java.util.List;
import java.util.logging.Logger;

@Component
@RequiredArgsConstructor
public class PresentationCleanupScheduler {

    private static final Logger logger = Logger.getLogger(PresentationCleanupScheduler.class.getName());
    private static final long RETENTION_DAYS = 5;

    private final PresentationRepository presentationRepository;
    private final S3Client s3Client;

    @Value("${app.s3.bucket}") private String bucket;

    @Scheduled(cron = "0 0 4 * * *")
    @Transactional
    public void deleteExpiredPresentations() {
        List<Presentation> targets = presentationRepository.findByCreatedAtBefore(
                LocalDateTime.now().minusDays(RETENTION_DAYS));

        for (Presentation presentation : targets) {
            deleteS3ObjectQuietly(presentation.getSlideS3Key());
            deleteS3ObjectQuietly(presentation.getAudioS3Key());
        }

        presentationRepository.deleteAll(targets);
    }

    private void deleteS3ObjectQuietly(String key) {
        if (key == null) return;
        try {
            s3Client.deleteObject(DeleteObjectRequest.builder().bucket(bucket).key(key).build());
        } catch (Exception e) {
            logger.warning("S3 삭제 실패: " + key + " - " + e.getMessage());
        }
    }
}
