package com.konkuk.coach.controller;

import com.konkuk.coach.dto.response.PresentationCreateResponse;
import com.konkuk.coach.dto.response.PresentationSubmitResponse;
import com.konkuk.coach.exception.BusinessException;
import com.konkuk.coach.exception.PresentationErrorCode;
import com.konkuk.coach.service.PresentationService;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.webmvc.test.autoconfigure.WebMvcTest;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

import java.time.LocalDateTime;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@WebMvcTest(PresentationController.class)
class PresentationControllerTest {

    @Autowired
    private MockMvc mockMvc;

    @MockitoBean
    private PresentationService presentationService;

    @Test
    @DisplayName("POST /api/presentations: 정상 요청이면 201과 응답 바디를 반환한다")
    void createReturns201() throws Exception {
        PresentationCreateResponse response = new PresentationCreateResponse(
                1L, "https://s3.test/slide", "presentations/1/slides.pptx",
                "https://s3.test/audio", "presentations/1/audio.webm",
                "a".repeat(43), "https://front.test/r/1?token=abc",
                LocalDateTime.now());
        when(presentationService.create(any())).thenReturn(response);

        mockMvc.perform(post("/api/presentations")
                        .contentType("application/json")
                        .content("""
                                {"title":"발표 제목","script":"스크립트"}
                                """))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.presentation_id").value(1))
                .andExpect(jsonPath("$.slide_s3_key").value("presentations/1/slides.pptx"))
                .andExpect(jsonPath("$.result_token").value("a".repeat(43)));
    }

    @Test
    @DisplayName("POST /api/presentations: title이 비어있으면 400")
    void createWithBlankTitleReturns400() throws Exception {
        mockMvc.perform(post("/api/presentations")
                        .contentType("application/json")
                        .content("""
                                {"title":"","script":"스크립트"}
                                """))
                .andExpect(status().isBadRequest());
    }

    @Test
    @DisplayName("POST /api/presentations/{id}/submit: 정상이면 202와 상태를 반환한다")
    void submitReturns202() throws Exception {
        when(presentationService.submit(eq(1L), any()))
                .thenReturn(new PresentationSubmitResponse(1L, "PROCESSING"));

        mockMvc.perform(post("/api/presentations/1/submit")
                        .contentType("application/json")
                        .content("""
                                {"audio_duration_ms":30000}
                                """))
                .andExpect(status().isAccepted())
                .andExpect(jsonPath("$.presentation_id").value(1))
                .andExpect(jsonPath("$.status").value("PROCESSING"));
    }

    @Test
    @DisplayName("POST /api/presentations/{id}/submit: 존재하지 않는 id면 404")
    void submitNotFoundReturns404() throws Exception {
        when(presentationService.submit(eq(999L), any()))
                .thenThrow(new BusinessException(PresentationErrorCode.PRESENTATION_ID_NOT_FOUND));

        mockMvc.perform(post("/api/presentations/999/submit")
                        .contentType("application/json")
                        .content("""
                                {"audio_duration_ms":30000}
                                """))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.error.code").value("PRESENTATION_NOT_FOUND"));
    }

    @Test
    @DisplayName("POST /api/presentations/{id}/submit: 오디오 길이 초과면 400")
    void submitAudioDurationExceededReturns400() throws Exception {
        when(presentationService.submit(eq(1L), any()))
                .thenThrow(new BusinessException(PresentationErrorCode.AUDIO_DURATION_EXCEEDED));

        mockMvc.perform(post("/api/presentations/1/submit")
                        .contentType("application/json")
                        .content("""
                                {"audio_duration_ms":700000}
                                """))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error.code").value("AUDIO_DURATION_EXCEEDED"));
    }
}
