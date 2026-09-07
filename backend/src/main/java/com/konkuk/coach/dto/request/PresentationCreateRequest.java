package com.konkuk.coach.dto.request;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

public record PresentationCreateRequest(
        @NotBlank
        @Size(max = 255)
        String title,
        String script
) {}
