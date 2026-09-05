package com.konkuk.coach.repository;

import com.konkuk.coach.domain.Presentation;
import org.springframework.data.jpa.repository.JpaRepository;
import java.util.Optional;

public interface PresentationRepository extends JpaRepository<Presentation, Long> {

    Optional<Presentation> findByResultToken(String resultToken);
}