package com.konkuk.coach.controller;

import com.konkuk.coach.dto.request.PresentationCreateRequest;
import com.konkuk.coach.dto.request.PresentationSubmitRequest;
import com.konkuk.coach.dto.response.PresentationCreateResponse;
import com.konkuk.coach.dto.response.PresentationReportResponse;
import com.konkuk.coach.dto.response.PresentationSubmitResponse;
import com.konkuk.coach.service.PresentationService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/presentations")
@RequiredArgsConstructor
public class PresentationController {

    private final PresentationService presentationService;

    @PostMapping
    public ResponseEntity<PresentationCreateResponse> create(
            @Valid @RequestBody PresentationCreateRequest request
    ) {
        return ResponseEntity
                .status(HttpStatus.CREATED)
                .body(presentationService.create(request));
    }

    @PostMapping("/{id}/submit")
    public ResponseEntity<PresentationSubmitResponse> submit(
            @PathVariable Long id, @Valid @RequestBody PresentationSubmitRequest request
    ) {
        return ResponseEntity
                .status(HttpStatus.ACCEPTED)
                .body(presentationService.submit(id, request));
    }

    @GetMapping("/{presentation_id}")
    public ResponseEntity<PresentationReportResponse> report(
            @PathVariable Long presentation_id,
            @RequestHeader("X-Result-Token") String secret
    ) {
        return ResponseEntity
                .status(HttpStatus.OK)
                .body(presentationService.report(presentation_id, secret));
    }
}
