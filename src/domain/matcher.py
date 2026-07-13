import re
from typing import List, Tuple, Optional
from rapidfuzz import fuzz

from src.core.models import Track, CandidateTrack, MatchResult, ConfidenceTier
from src.domain.normalizer import (
    normalize_unicode,
    strip_edition_noise,
    extract_title_and_featured,
    normalize_artist,
    extract_track_flags,
    extract_part_indicator
)

def is_artist_match(sa: str, ca: str) -> bool:
    """Accurately compare two artist tokens with length and boundary awareness."""
    if not sa or not ca:
        return False
    clean_sa = re.sub(r"[^\w]", "", sa)
    clean_ca = re.sub(r"[^\w]", "", ca)
    if clean_sa == clean_ca:
        return True
    # For very short artist names (<= 4 chars, e.g. HER, U2, NEU, CHER, EVE), reject fuzzy edit distance
    if min(len(clean_sa), len(clean_ca)) <= 4:
        return False
    if fuzz.ratio(sa, ca) >= 85:
        return True
    if len(sa) >= 5 and re.search(r'\b' + re.escape(sa) + r'\b', ca):
        return True
    if len(ca) >= 5 and re.search(r'\b' + re.escape(ca) + r'\b', sa):
        return True
    return False

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
        # 0. ISRC Exact Match Check (100% confidence for identical master recordings)
        cand_isrc = getattr(candidate, "isrc", None)
        if source.isrc and cand_isrc:
            clean_src_isrc = source.isrc.replace("-", "").strip().upper()
            clean_cand_isrc = str(cand_isrc).replace("-", "").strip().upper()
            if clean_src_isrc == clean_cand_isrc:
                dur_score = cls.calculate_duration_score(source.duration_seconds, candidate.duration_seconds)
                if dur_score >= 0.85:
                    return 1.0, f"Exact ISRC match ({clean_src_isrc})"

        # 1. Clean Title Matching
        src_clean_title, src_feat = extract_title_and_featured(source.name)
        cand_clean_title, cand_feat = extract_title_and_featured(candidate.title)

        title_ratio = fuzz.ratio(src_clean_title.lower(), cand_clean_title.lower()) / 100.0
        token_sort = fuzz.token_sort_ratio(src_clean_title.lower(), cand_clean_title.lower()) / 100.0

        # Token set ratio with length & word count damping
        raw_token_set = fuzz.token_set_ratio(src_clean_title.lower(), cand_clean_title.lower()) / 100.0
        src_tokens = [t for t in re.split(r"[^\w]+", src_clean_title.lower()) if t]
        cand_tokens = [t for t in re.split(r"[^\w]+", cand_clean_title.lower()) if t]

        t_min = min(len(src_tokens), len(cand_tokens))
        t_max = max(len(src_tokens), len(cand_tokens), 1)
        token_len_ratio = t_min / t_max
        char_len_ratio = min(len(src_clean_title), len(cand_clean_title)) / max(len(src_clean_title), len(cand_clean_title), 1)

        # Damp subset matching when lengths diverge (prevents "Run" matching "Run For Your Life")
        damped_token_set = raw_token_set * (0.35 + 0.65 * (0.5 * token_len_ratio + 0.5 * char_len_ratio))
        if (len(src_tokens) == 1 and len(cand_tokens) > 1) or (len(cand_tokens) == 1 and len(src_tokens) > 1):
            damped_token_set = min(damped_token_set, 0.50)

        title_score = max(title_ratio, token_sort, damped_token_set)

        # 2. Artist Matching with Token & Boundary Awareness
        src_artists = normalize_artist(source.artist) + [f.lower() for f in src_feat]
        cand_artists = normalize_artist(candidate.artist) + [f.lower() for f in cand_feat]

        artist_score = 0.0
        if not src_artists:
            artist_score = 0.75
        else:
            matches = 0
            for sa in src_artists:
                for ca in cand_artists:
                    if is_artist_match(sa, ca):
                        matches += 1
                        break
            if matches > 0:
                artist_score = min(1.0, 0.75 + (matches * 0.25))
            else:
                token_art = fuzz.token_sort_ratio(source.artist.lower(), candidate.artist.lower()) / 100.0
                if token_art >= 0.80:
                    artist_score = token_art * 0.85
                else:
                    artist_score = token_art * 0.25  # Heavily discount divergent artists

        # 3. Duration Score
        dur_score = cls.calculate_duration_score(source.duration_seconds, candidate.duration_seconds)

        # 4. Attribute Mismatch Penalties
        src_flags = extract_track_flags(source.name, source.artist)
        cand_flags = extract_track_flags(candidate.title, candidate.artist)

        attribute_multiplier = 1.0
        mismatch_reasons = []

        # Movement / Part Designation Check (e.g. Pt 1 vs Pt 2)
        src_part = extract_part_indicator(source.name)
        cand_part = extract_part_indicator(candidate.title)
        if src_part and cand_part and src_part != cand_part:
            attribute_multiplier *= 0.30
            mismatch_reasons.append(f"Movement/Part mismatch ({src_part.upper()} vs {cand_part.upper()})")

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

        # Cover Mismatch Penalty
        if not src_flags.get("is_cover") and cand_flags.get("is_cover"):
            attribute_multiplier *= 0.35
            mismatch_reasons.append("Unwanted Cover version")

        # Parody Mismatch Penalty
        if not src_flags.get("is_parody") and cand_flags.get("is_parody"):
            attribute_multiplier *= 0.20
            mismatch_reasons.append("Unwanted Parody version")

        # Sped Up / Slowed / Nightcore Penalty
        if not src_flags.get("is_sped_slow") and cand_flags.get("is_sped_slow"):
            attribute_multiplier *= 0.30
            mismatch_reasons.append("Sped Up/Slowed modification")

        # Result Type Multiplier
        type_multiplier = 1.0 if candidate.result_type == "song" else 0.90

        # 5. Composite Score Calculation
        weighted_score = (
            (title_score * 0.50) +
            (artist_score * 0.30) +
            (dur_score * 0.20)
        ) * attribute_multiplier * type_multiplier

        # Gating 1: Artist Identity Gate
        # If artist similarity is poor (< 0.50), this is a different artist singing a song with the same title
        if artist_score < 0.50:
            artist_gate = max(0.0, artist_score / 0.50)
            weighted_score *= artist_gate

        # Gating 2: Title Identity Gate
        # If clean title similarity is weak (< 0.60), this is likely a different song from the same album/artist
        if title_score < 0.60:
            title_gate = max(0.0, title_score / 0.60)
            weighted_score *= title_gate

        weighted_score = max(0.0, min(1.0, weighted_score))

        # Build ASCII-Safe Rationale
        dur_diff = int(abs(source.duration_seconds - candidate.duration_seconds)) if source.duration_seconds > 0 and candidate.duration_seconds > 0 else None
        dur_note = f"diff={dur_diff}s" if dur_diff is not None else "no dur"
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
                rationale="No search candidates found on destination catalog",
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
