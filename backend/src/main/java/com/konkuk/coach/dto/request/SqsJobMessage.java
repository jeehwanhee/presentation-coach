package com.konkuk.coach.dto.request;

public record SqsJobMessage(
        Long presentationId,
        String slideS3Key,
        String audioS3Key,
        String script,
        String callbackUrl
) {
}
