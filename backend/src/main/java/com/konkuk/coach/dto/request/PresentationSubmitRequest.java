package com.konkuk.coach.dto.request;

import jakarta.validation.constraints.NotNull;

public record PresentationSubmitRequest(
   @NotNull
   Integer audioDurationMs
) {}
