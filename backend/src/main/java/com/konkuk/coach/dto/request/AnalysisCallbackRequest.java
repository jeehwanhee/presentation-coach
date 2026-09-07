package com.konkuk.coach.dto.request;

import com.konkuk.coach.exception.ErrorBody;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import tools.jackson.databind.JsonNode;

public record AnalysisCallbackRequest(
        @NotNull Long presentationId,
        @NotBlank @Pattern(regexp = "DONE|FAILED", message = "status는 DONE 또는 FAILED만 허용됩니다.")
        String status,
        JsonNode transcript,
        JsonNode report,
        ErrorBody error
) {}
