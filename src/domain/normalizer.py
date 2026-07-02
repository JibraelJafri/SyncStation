import re
import unicodedata
from typing import Tuple, List, Dict, Any

# Common edition suffixes and noisy patterns
EDITION_PATTERNS = [
    re.compile(r"\s*[-/|]\s*Remaster(?:ed)?(?:\s*\d{4})?", re.IGNORECASE),
    re.compile(r"\s*\(\s*Remaster(?:ed)?(?:\s*\d{4})?\s*\)", re.IGNORECASE),
    re.compile(r"\s*[-/|]\s*\d{4}\s+(?:Digital\s+)?Remaster(?:ed)?", re.IGNORECASE),
    re.compile(r"\s*\(\s*\d{4}\s+(?:Digital\s+)?Remaster(?:ed)?\s*\)", re.IGNORECASE),
    re.compile(r"\s*[-/|]\s*\d{4}\s*-\s*Remaster(?:ed)?", re.IGNORECASE),
    re.compile(r"\s*\(\s*\d{4}\s*-\s*Remaster(?:ed)?\s*\)", re.IGNORECASE),
    re.compile(r"\s*[-/|]\s*Deluxe(?:\s+Edition)?", re.IGNORECASE),
    re.compile(r"\s*\(\s*Deluxe(?:\s+Edition)?\s*\)", re.IGNORECASE),
    re.compile(r"\s*[-/|]\s*Super\s+Deluxe(?:\s+Edition)?", re.IGNORECASE),
    re.compile(r"\s*\(\s*Super\s+Deluxe(?:\s+Edition)?\s*\)", re.IGNORECASE),
    re.compile(r"\s*[-/|]\s*Anniversary(?:\s+Edition)?", re.IGNORECASE),
    re.compile(r"\s*\(\s*Anniversary(?:\s+Edition)?\s*\)", re.IGNORECASE),
    re.compile(r"\s*[-/|]\s*Expanded(?:\s+Edition)?", re.IGNORECASE),
    re.compile(r"\s*\(\s*Expanded(?:\s+Edition)?\s*\)", re.IGNORECASE),
    re.compile(r"\s*[-/|]\s*Bonus\s+Track(?:\s+Version)?", re.IGNORECASE),
    re.compile(r"\s*\(\s*Bonus\s+Track(?:\s+Version)?\s*\)", re.IGNORECASE),
    re.compile(r"\s*[-/|]\s*Radio\s+Edit", re.IGNORECASE),
    re.compile(r"\s*\(\s*Radio\s+Edit\s*\)", re.IGNORECASE),
    re.compile(r"\s*[-/|]\s*Original\s+(?:Mix|Version)", re.IGNORECASE),
    re.compile(r"\s*\(\s*Original\s+(?:Mix|Version)\s*\)", re.IGNORECASE),
    re.compile(r"\s*[-/|]\s*Single\s+Version", re.IGNORECASE),
    re.compile(r"\s*\(\s*Single\s+Version\s*\)", re.IGNORECASE),
    re.compile(r"\s*[\(\[]\s*Official\s+(?:Music\s+)?Video\s*[\]\)]", re.IGNORECASE),
    re.compile(r"\s*[\(\[]\s*Official\s+Audio\s*[\]\)]", re.IGNORECASE),
    re.compile(r"\s*[\(\[]\s*Lyric\s+Video\s*[\]\)]", re.IGNORECASE),
    re.compile(r"\s*[\(\[]\s*Visualizer\s*[\]\)]", re.IGNORECASE),
    re.compile(r"\s*[\(\[]\s*Visualiser\s*[\]\)]", re.IGNORECASE),
    re.compile(r"\s*[\(\[]\s*HD\s*[\]\)]", re.IGNORECASE),
    re.compile(r"\s*[\(\[]\s*4K(?:\s+Remaster)?\s*[\]\)]", re.IGNORECASE),
    re.compile(r"\s*[-/|]\s*from\s+[\"'].*?[\"'](?:\s+Soundtrack)?", re.IGNORECASE),
    re.compile(r"\s*[-/|]\s*From\s+the\s+.*?Soundtrack", re.IGNORECASE),
    re.compile(r"\s*[-/|]\s*Music\s+From\s+The\s+Motion\s+Picture.*", re.IGNORECASE),
    re.compile(r"\s*\(\s*Music\s+From\s+The\s+Motion\s+Picture.*?\)", re.IGNORECASE),
    re.compile(r"\s*[-/|]\s*Studio\s+Recording\s+From\s+.*", re.IGNORECASE),
    re.compile(r"\s*[-/|]\s*As\s+Heard\s+on\s+.*", re.IGNORECASE),
    re.compile(r"\s*\[.*?Version\]", re.IGNORECASE),
    re.compile(r"\s*\[.*?Remaster.*?\]", re.IGNORECASE),
    re.compile(r"\s*\[.*?Edition\]", re.IGNORECASE),
    re.compile(r"\s*[-/|]\s*Stereo(?:\s+Version)?", re.IGNORECASE),
    re.compile(r"\s*[-/|]\s*Mono(?:\s+Version)?", re.IGNORECASE),
]

FEAT_PATTERNS = [
    re.compile(r"\s*[\(\[]\s*(?:feat(?:\.|uring)?|ft\.?|with)\s+.*?[\]\)]", re.IGNORECASE),
    re.compile(r"\s*[-/|]\s*(?:feat(?:\.|uring)?|ft\.?|with)\s+.*", re.IGNORECASE),
    re.compile(r"\s+(?:feat(?:\.|uring)?|ft\.?|with)\s+.*", re.IGNORECASE),
]

LIVE_PATTERNS = [
    re.compile(r"\bLive(?:\s+at\s+[^)\]-]+)?\b", re.IGNORECASE),
    re.compile(r"\(Live\)", re.IGNORECASE),
    re.compile(r"\[Live\]", re.IGNORECASE),
    re.compile(r"\bLive\s+Session\b", re.IGNORECASE),
]

REMIX_PATTERNS = [
    re.compile(r"\bRemix\b", re.IGNORECASE),
    re.compile(r"\bClub\s+Mix\b", re.IGNORECASE),
    re.compile(r"\bExtended\s+Mix\b", re.IGNORECASE),
    re.compile(r"\bVIP\s+Mix\b", re.IGNORECASE),
    re.compile(r"\bDub\s+Mix\b", re.IGNORECASE),
]

ACOUSTIC_PATTERNS = [
    re.compile(r"\bAcoustic\b", re.IGNORECASE),
    re.compile(r"\bUnplugged\b", re.IGNORECASE),
    re.compile(r"\bPiano\s+Version\b", re.IGNORECASE),
]

INSTRUMENTAL_PATTERNS = [
    re.compile(r"\bInstrumental\b", re.IGNORECASE),
    re.compile(r"\bKaraoke\b", re.IGNORECASE),
    re.compile(r"\bBacking\s+Track\b", re.IGNORECASE),
]

def normalize_unicode(text: str) -> str:
    """Strip diacritics and standardize typographic punctuation."""
    if not text:
        return ""
    nfkd = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in nfkd if not unicodedata.combining(c))
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    text = text.replace("–", "-").replace("—", "-").replace("…", "...")
    return text.strip()

def extract_track_flags(title: str, artist: str = "") -> Dict[str, bool]:
    """Detect special attributes: live, remix, acoustic, instrumental."""
    combined = f"{title} {artist}"
    return {
        "is_live": any(p.search(combined) for p in LIVE_PATTERNS),
        "is_remix": any(p.search(combined) for p in REMIX_PATTERNS),
        "is_acoustic": any(p.search(combined) for p in ACOUSTIC_PATTERNS),
        "is_instrumental": any(p.search(combined) for p in INSTRUMENTAL_PATTERNS),
    }

def strip_edition_noise(title: str) -> str:
    """Remove remastered / deluxe / anniversary labels from song title."""
    cleaned = normalize_unicode(title)
    for pattern in EDITION_PATTERNS:
        cleaned = pattern.sub("", cleaned)
    return cleaned.strip()

def extract_title_and_featured(title: str) -> Tuple[str, List[str]]:
    """Extract clean title and list of featured artist names."""
    cleaned = strip_edition_noise(title)
    featured = []

    for pattern in FEAT_PATTERNS:
        match = pattern.search(cleaned)
        if match:
            feat_text = match.group(0)
            names = re.sub(r"[\(\[\)\]]|\b(?:featuring|feat|ft|with)\b\.?", "", feat_text, flags=re.IGNORECASE)
            names = re.sub(r"^[\s\-/\.\\]+", "", names)
            parts = [n.strip().lstrip("-/ .").strip() for n in re.split(r"[,&/]", names) if n.strip()]
            featured.extend(parts)
            cleaned = pattern.sub("", cleaned)
            break

    return cleaned.strip(), featured

def normalize_artist(artist: str) -> List[str]:
    """Split multi-artist strings into normalized list of distinct artist names."""
    if not artist:
        return []
    clean_art = normalize_unicode(artist)
    parts = re.split(r",\s*|\s*&\s*|\s*/\s*|\s+x\s+|\s+vs\.?\s+|\s+with\s+", clean_art, flags=re.IGNORECASE)
    return [p.strip().lower() for p in parts if p.strip()]

def clean_query_string(artist: str, title: str) -> str:
    """Generate high-accuracy clean search query."""
    clean_title, _ = extract_title_and_featured(title)
    clean_title = re.sub(r"[^\w\s\-\']", " ", clean_title)
    clean_artist = re.sub(r"[^\w\s\-\']", " ", artist)

    query = f"{clean_artist} {clean_title}"
    query = re.sub(r"\s+", " ", query).strip()
    return query
