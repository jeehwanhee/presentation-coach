package com.konkuk.coach.domain;

import jakarta.persistence.*;
import lombok.Getter;
import lombok.Setter;

import java.time.LocalDateTime;

@Entity
@Getter @Setter
@Table(name = "presentation")
public class Presentation {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)   // AUTO_INCREMENT
    private Long id;

    @Column(nullable = false)
    private String title;

    @Column(columnDefinition = "TEXT")
    private String script;

    @Column(name = "slide_s3_key", nullable = false, length = 512)
    private String slideS3Key;

    @Column(name = "audio_s3_key", nullable = false, length = 512)
    private String audioS3Key;

    @Enumerated(EnumType.STRING)
    @Column(nullable = false, length = 20)
    private PresentationStatus status = PresentationStatus.PENDING;

    @Column(name = "result_token", nullable = false, unique = true, length = 43)
    private String resultToken;

    @Column(name = "audio_duration_ms")
    private Integer audioDurationMs;

    @Column(name = "transcript_json", columnDefinition = "JSON")
    private String transcriptJson;

    @Column(name = "report_json", columnDefinition = "JSON")
    private String reportJson;

    @Column(name = "error_code", length = 64)
    private String errorCode;

    @Column(name = "error_message", columnDefinition = "TEXT")
    private String errorMessage;

    @Column(name = "expires_at", nullable = false)
    private LocalDateTime expiresAt;

    @Column(name = "created_at", nullable = false, updatable = false)
    private LocalDateTime createdAt;

    @Column(name = "updated_at", nullable = false)
    private LocalDateTime updatedAt;

    @PrePersist
    protected void onCreate() {
        this.createdAt = LocalDateTime.now();
        this.updatedAt = this.createdAt;
        if (this.expiresAt == null) {
            this.expiresAt = this.createdAt.plusDays(3);  // 3일 만료
        }
    }

    @PreUpdate
    protected void onUpdate() {
        this.updatedAt = LocalDateTime.now();
    }
}