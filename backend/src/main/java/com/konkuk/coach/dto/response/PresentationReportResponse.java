package com.konkuk.coach.dto.response;

import com.fasterxml.jackson.annotation.JsonRawValue;
import com.konkuk.coach.exception.ErrorBody;

public record PresentationReportResponse (
    Long presentationId,
    String title,
    String status,
    Integer audioDurationMs,
    @JsonRawValue String report,
    ErrorBody error
) {
    public static PresentationReportResponse processing(Long id, String title, Integer audioDurationMs) {
        return new PresentationReportResponse(id, title, "PROCESSING", audioDurationMs, null, null);
    }

    public static PresentationReportResponse done(Long id, String title, Integer audioDurationMs, String reportJson) {
        return new PresentationReportResponse(id, title, "DONE", audioDurationMs, reportJson, null);
    }

    public static PresentationReportResponse failed(Long id, String title, Integer audioDurationMs, String errorCode, String errorMessage) {
        return new PresentationReportResponse(id, title, "FAILED", audioDurationMs, null, new ErrorBody(errorCode, errorMessage));
    }
}
