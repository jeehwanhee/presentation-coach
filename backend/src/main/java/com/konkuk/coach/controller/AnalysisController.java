package com.konkuk.coach.controller;

import com.konkuk.coach.dto.request.AnalysisCallbackRequest;
import com.konkuk.coach.exception.BusinessException;
import com.konkuk.coach.exception.PresentationErrorCode;
import com.konkuk.coach.service.PresentationService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/internal")
@RequiredArgsConstructor
public class AnalysisController {

    private final PresentationService presentationService;

    @Value("${app.worker.secret}")
    private String workerSecret;

    @PostMapping("/analysis-results")
    public ResponseEntity<Void> receive(
            @RequestHeader("X-Worker-Secret") String secret,
            @Valid @RequestBody AnalysisCallbackRequest request
    ) {
        if (!workerSecret.equals(secret))
            throw new BusinessException(PresentationErrorCode.INVALID_WORKER_SECRET);

        presentationService.applyAnalysisResult(request);
        return ResponseEntity.ok().build();
    }
}
