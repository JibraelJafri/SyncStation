from typing import List, Tuple, Optional
from rapidfuzz import fuzz

from src.core.models import Track, CandidateTrack, MatchResult, ConfidenceTier
from src.domain.normalizer import (
    normalize_unicode,
    strip_edition_noise,
    extract_title_and_featured,
    normalize_artist,
    extract_track_flags
)

class MatchingEngine:
    @staticmethod
    def calculate_duration_score(src_dur: float, cand_dur: float) -> float:
        if src_dur <= 0 or cand_dur <= 0:
            return 0.85  # Neutral baseline when duration is unavailable

        diff = abs(src_dur - cand_dur)
        if diff <= 2.0:
            return 1.0
        elif diff <= 5.0:
            return 0.95
        elif diff <= 10.0:
            return 0.85
        elif diff <= 20.0:
            return 0.65
        elif diff <= 45.0:
            return 0.35
        elif diff <= 90.0:
            return 0.15
        else:
            return 0.05

    @classmethod
    def score_candidate(cls, source: Track, candidate: CandidateTrack) -> Tuple[float, str]:
        # 1. Clean Title Matching
        src_clean_title, src_feat = extract_title_and_featured(source.name)
        cand_clean_title, cand_feat = extract_title_and_featured(candidate.title)

        title_ratio = fuzz.ratio(src_clean_title.lower(), cand_clean_title.lower()) / 100.0
        token_ratio = fuzz.token_set_ratio(src_clean_title.lower(), cand_clean_title.lower()) / 100.0
        token_sort = fuzz.token_sort_ratio(src_clean_title.lower(), cand_clean_title.lower()) / 100.0
        title_score = max(title_ratio, token_ratio, token_sort)

        # 2. Artist Matching
        src_artists = normalize_artist(source.artist) + [f.lower() for f in src_feat]
        cand_artists = normalize_artist(candidate.artist) + [f.lower() for f in cand_feat]

        artist_score = 0.0
        if not src_artists:
            artist_score = 0.75
        else:
            matches = 0
            for sa in src_artists:
                for ca in cand_artists:
                    if fuzz.ratio(sa, ca) >= 80 or sa in ca or ca in sa:
                        matches += 1
                        break
            if matches > 0:
                artist_score = min(1.0, 0.75 + (matches * 0.25))
            else:
                token_art = fuzz.token_set_ratio(source.artist.lower(), candidate.artist.lower()) / 100.0
                artist_score = token_art

        # 3. Duration Score
        dur_score = cls.calculate_duration_score(source.duration_seconds, candidate.duration_seconds)

        # 4. Attribute Mismatch Penalties
        src_flags = extract_track_flags(source.name, source.artist)
        cand_flags = extract_track_flags(candidate.title, candidate.artist)

        attribute_multiplier = 1.0
        mismatch_reasons = []

        # Live Mismatch Penalty
        if not src_flags["is_live"] and cand_flags["is_live"]:
            attribute_multiplier *= 0.50
            mismatch_reasons.append("Unwanted Live version")
        elif src_flags["is_live"] and cand_flags["is_live"]:
            attribute_multiplier *= 1.05

        # Remix Mismatch Penalty
        if not src_flags["is_remix"] and cand_flags["is_remix"]:
            attribute_multiplier *= 0.45
            mismatch_reasons.append("Unwanted Remix version")

        # Instrumental / Karaoke Penalty
        if not src_flags["is_instrumental"] and cand_flags["is_instrumental"]:
            attribute_multiplier *= 0.30
            mismatch_reasons.append("Instrumental/Karaoke mismatch")

        # Acoustic Mismatch Penalty
        if not src_flags["is_acoustic"] and cand_flags["is_acoustic"]:
            attribute_multiplier *= 0.70
            mismatch_reasons.append("Acoustic mismatch")

        # Result Type Multiplier
        type_multiplier = 1.0 if candidate.result_type == "song" else 0.90

        # 5. Composite Score Calculation
        weighted_score = (
            (title_score * 0.50) +
            (artist_score * 0.30) +
            (dur_score * 0.20)
        ) * attribute_multiplier * type_multiplier

        weighted_score = max(0.0, min(1.0, weighted_score))

        # Build Rationale
        dur_diff = int(abs(source.duration_seconds - candidate.duration_seconds)) if source.duration_seconds > 0 and candidate.duration_seconds > 0 else None
        dur_note = f"Δ={dur_diff}s" if dur_diff is not None else "no dur"
        type_note = "Song" if candidate.result_type == "song" else "Video"
        rationale_parts = [
            f"Title: {int(title_score*100)}%",
            f"Artist: {int(artist_score*100)}%",
            f"{type_note} ({dur_note})"
        ]
        if mismatch_reasons:
            rationale_parts.append(f"Penalty: {', '.join(mismatch_reasons)}")

        rationale = " | ".join(rationale_parts)
        return weighted_score, rationale

    @classmethod
    def match_track(cls, source: Track, candidates: List[CandidateTrack]) -> MatchResult:
        if not candidates:
            return MatchResult(
                source_track=source,
                matched_track=None,
                confidence_score=0.0,
                confidence_tier=ConfidenceTier.NO_MATCH,
                rationale="No search candidates found on YouTube Music",
                alternative_candidates=[],
                is_accepted=False
            )

        scored_candidates = []
        for cand in candidates:
            score, rationale = cls.score_candidate(source, cand)
            scored_candidates.append((score, rationale, cand))

        scored_candidates.sort(key=lambda x: x[0], reverse=True)
        best_score, best_rationale, best_cand = scored_candidates[0]

        # Tier Classification
        if best_score >= 0.92:
            tier = ConfidenceTier.EXACT
        elif best_score >= 0.80:
            tier = ConfidenceTier.HIGH
        elif best_score >= 0.65:
            tier = ConfidenceTier.PROBABLE
        elif best_score >= 0.45:
            tier = ConfidenceTier.AMBIGUOUS
        else:
            tier = ConfidenceTier.NO_MATCH

        # Alternatives
        alternatives = [c[2] for c in scored_candidates[1:6]]

        # Safe Acceptance: Only EXACT and HIGH are unconditionally accepted.
        # PROBABLE is accepted if title similarity is high. AMBIGUOUS requires user confirmation.
        is_accepted = tier in [ConfidenceTier.EXACT, ConfidenceTier.HIGH]
        if tier == ConfidenceTier.PROBABLE and best_score >= 0.72:
            is_accepted = True

        return MatchResult(
            source_track=source,
            matched_track=best_cand if tier != ConfidenceTier.NO_MATCH else None,
            confidence_score=round(best_score, 3),
            confidence_tier=tier,
            rationale=best_rationale,
            alternative_candidates=alternatives,
            is_accepted=is_accepted
        )
