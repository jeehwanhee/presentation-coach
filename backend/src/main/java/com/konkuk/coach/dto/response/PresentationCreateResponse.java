package com.konkuk.coach.dto.response;

import java.time.LocalDateTime;

public record PresentationCreateResponse(
   Long presentationId,
   String slideUploadUrl,
   String slideS3Key,
   String audioUploadUrl,
   String audioS3Key,
   String resultToken,
   String resultUrl,
   LocalDateTime expiresAt
) {}
