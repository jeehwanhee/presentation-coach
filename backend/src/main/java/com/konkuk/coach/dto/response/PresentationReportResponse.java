package com.konkuk.coach.dto.response;

import com.fasterxml.jackson.annotation.JsonRawValue;
import com.konkuk.coach.exception.ErrorBody;

public record PresentationReportResponse (
    Long presentationId,
    String status,
    @JsonRawValue String report,
    ErrorBody error
) {
    public static PresentationReportResponse processing(Long id) {
        return new PresentationReportResponse(id, "PROCESSING", null, null);
    }

    public static PresentationReportResponse done(Long id, String reportJson) {
        return new PresentationReportResponse(id, "DONE", reportJson, null);
    }

    public static PresentationReportResponse failed(Long id, String errorCode, String errorMessage) {
        return new PresentationReportResponse(id, "FAILED", null, new ErrorBody(errorCode, errorMessage));
    }
}
