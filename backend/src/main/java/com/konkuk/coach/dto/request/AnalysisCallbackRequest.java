package com.konkuk.coach.dto.request;

import com.konkuk.coach.exception.ErrorBody;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import tools.jackson.databind.JsonNode;

public record AnalysisCallbackRequest(
        @NotNull Long presentationId,
        @NotBlank String status,
        JsonNode transcript,
        JsonNode report,
        ErrorBody error
) {}
