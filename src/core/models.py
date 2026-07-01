from enum import Enum
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from datetime import datetime, timezone

def utc_now():
    return datetime.now(timezone.utc)

class ConnectionState(str, Enum):
    NOT_CONFIGURED = "NOT_CONFIGURED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    EXPIRED = "EXPIRED"
    AUTH_FAILED = "AUTH_FAILED"
    RECONNECTING = "RECONNECTING"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"

class ConfidenceTier(str, Enum):
    EXACT = "EXACT"           # >= 0.92: Near perfect match across title, artist, duration, or ISRC
    HIGH = "HIGH"             # >= 0.80: Strong match with minor title/edition variance
    PROBABLE = "PROBABLE"     # >= 0.65: Likely match, slight duration or edition variance
    AMBIGUOUS = "AMBIGUOUS"   # 0.45 - 0.65: Needs user review
    NO_MATCH = "NO_MATCH"     # < 0.45: No acceptable candidate found

class SyncMode(str, Enum):
    TRANSFER = "TRANSFER"       # Initial mirror establishment
    INCREMENTAL = "INCREMENTAL" # Delta-based synchronization
    FULL_RECONCILE = "FULL_RECONCILE"

class JobStatus(str, Enum):
    IDLE = "IDLE"
    ANALYZING = "ANALYZING"
    AWAITING_REVIEW = "AWAITING_REVIEW"
    SYNCING = "SYNCING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    PARTIAL_SUCCESS = "PARTIAL_SUCCESS"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"

class PlanAction(str, Enum):
    ADD_TRACK = "ADD_TRACK"
    REMOVE_TRACK = "REMOVE_TRACK"
    UPDATE_METADATA = "UPDATE_METADATA"
    REORDER_TRACK = "REORDER_TRACK"
    SKIP_TRACK = "SKIP_TRACK"
    REVIEW_MATCH = "REVIEW_MATCH"
    NO_OP = "NO_OP"

class RemovalPolicy(str, Enum):
    NEVER_REMOVE = "NEVER_REMOVE"
    ASK_BEFORE_REMOVE = "ASK_BEFORE_REMOVE"
    MIRROR_REMOVALS = "MIRROR_REMOVALS"

class AdditionPolicy(str, Enum):
    AUTO_ADD = "AUTO_ADD"
    ASK_REVIEW = "ASK_REVIEW"

class AmbiguousMatchPolicy(str, Enum):
    ASK_REVIEW = "ASK_REVIEW"
    SKIP = "SKIP"
    AUTO_BEST_GUESS = "AUTO_BEST_GUESS"

class SyncPolicy(BaseModel):
    additions: AdditionPolicy = AdditionPolicy.AUTO_ADD
    removals: RemovalPolicy = RemovalPolicy.ASK_BEFORE_REMOVE
    ambiguous: AmbiguousMatchPolicy = AmbiguousMatchPolicy.ASK_REVIEW

class Track(BaseModel):
    id: str = ""
    uri: str = ""
    name: str
    artist: str
    album: str = ""
    duration_seconds: float = 0.0
    isrc: Optional[str] = None
    track_number: Optional[int] = None
    disc_number: Optional[int] = None
    release_year: Optional[int] = None
    is_explicit: bool = False
    image_url: Optional[str] = None
    preview_url: Optional[str] = None

class Playlist(BaseModel):
    id: str = ""
    uri: str = ""
    name: str
    description: str = ""
    track_count: int = 0
    image_url: Optional[str] = None
    tracks: List[Track] = Field(default_factory=list)
    source_type: str = "export" # "api", "export", "web"
    snapshot_id: Optional[str] = None

class CandidateTrack(BaseModel):
    video_id: str
    title: str
    artist: str
    album: str = ""
    duration_seconds: float = 0.0
    result_type: str = "song"  # "song" or "video"
    thumbnail_url: Optional[str] = None
    views: Optional[str] = None
    is_explicit: bool = False

class MatchResult(BaseModel):
    source_track: Track
    matched_track: Optional[CandidateTrack] = None
    confidence_score: float = 0.0
    confidence_tier: ConfidenceTier = ConfidenceTier.NO_MATCH
    rationale: str = ""
    alternative_candidates: List[CandidateTrack] = Field(default_factory=list)
    is_manual_override: bool = False
    is_accepted: bool = True
    is_already_in_destination: bool = False

class MirrorStatus(str, Enum):
    IN_SYNC = "IN_SYNC"
    CHANGES_DETECTED = "CHANGES_DETECTED"
    SYNCING = "SYNCING"
    ATTENTION_NEEDED = "ATTENTION_NEEDED"
    NEVER_SYNCED = "NEVER_SYNCED"
    ERROR = "ERROR"

class MirroredPlaylist(BaseModel):
    id: str
    name: str
    spotify_id: str = ""
    spotify_uri: str = ""
    spotify_url: Optional[str] = None
    yt_playlist_id: str
    yt_playlist_name: str
    source_type: str = "export"  # "api", "export", "web"
    source_snapshot_id: Optional[str] = None
    last_synced_at: Optional[datetime] = None
    last_sync_status: MirrorStatus = MirrorStatus.NEVER_SYNCED
    spotify_track_count: int = 0
    yt_track_count: int = 0
    delta_added_count: int = 0
    delta_removed_count: int = 0
    sync_policy_additions: AdditionPolicy = AdditionPolicy.AUTO_ADD
    sync_policy_removals: RemovalPolicy = RemovalPolicy.ASK_BEFORE_REMOVE
    sync_policy_matching: AmbiguousMatchPolicy = AmbiguousMatchPolicy.ASK_REVIEW
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

class MirrorTrackManifest(BaseModel):
    id: Optional[int] = None
    mirror_id: str
    spotify_uri: str
    spotify_name: str
    spotify_artist: str
    spotify_album: str = ""
    spotify_duration: float = 0.0
    yt_video_id: str
    yt_title: str
    yt_artist: str
    position: int = 0
    synced_at: datetime = Field(default_factory=utc_now)

class SyncPlanItem(BaseModel):
    action: PlanAction
    source_track: Optional[Track] = None
    matched_candidate: Optional[CandidateTrack] = None
    destination_video_id: Optional[str] = None
    confidence_tier: ConfidenceTier = ConfidenceTier.NO_MATCH
    confidence_score: float = 0.0
    rationale: str = ""
    requires_user_review: bool = False
    is_accepted: bool = True

class SyncPlan(BaseModel):
    id: str
    mirror_id: Optional[str] = None
    playlist_name: str
    spotify_id: str
    yt_playlist_id: Optional[str] = None
    source_track_count: int = 0
    destination_track_count: int = 0
    additions_count: int = 0
    restorations_count: int = 0
    extra_dest_count: int = 0
    removals_count: int = 0
    unchanged_count: int = 0
    ambiguous_count: int = 0
    unmatched_count: int = 0
    rename_detected: bool = False
    new_name: Optional[str] = None
    items: List[SyncPlanItem] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)

class MirrorDiscoveryCandidate(BaseModel):
    spotify_playlist: Playlist
    yt_playlist_id: str
    yt_playlist_name: str
    name_similarity: float
    track_overlap_count: int
    track_total_count: int
    track_overlap_percent: float
    overall_confidence: float
    existing_mirror_id: Optional[str] = None
    is_linked: bool = False

class SpotifySnapshotMetadata(BaseModel):
    file_name: str
    file_path: str
    file_size_kb: float
    modified_at: datetime
    export_date: Optional[str] = None
    playlist_count: int = 0
    total_tracks: int = 0
    user_id: Optional[str] = None
    is_active: bool = False

class SyncJob(BaseModel):
    id: str
    mirror_id: Optional[str] = None
    playlist_name: str
    spotify_url_or_id: str
    yt_playlist_id: Optional[str] = None
    mode: SyncMode = SyncMode.INCREMENTAL
    status: JobStatus = JobStatus.IDLE
    total_tracks: int = 0
    processed_tracks: int = 0
    matched_tracks: int = 0
    synced_tracks: int = 0
    removed_tracks: int = 0
    skipped_tracks: int = 0
    failed_tracks: int = 0
    current_track_name: str = ""
    current_thumbnail_url: Optional[str] = None
    eta_seconds: Optional[int] = None
    error_message: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

class SyncReport(BaseModel):
    job_id: str
    mirror_id: Optional[str] = None
    playlist_name: str
    yt_playlist_url: str
    status: str = "COMPLETED" # "COMPLETED", "COMPLETED_WITH_WARNINGS", "PARTIAL_SUCCESS", "FAILED"
    total_source_tracks: int = 0
    total_synced_tracks: int = 0
    already_synchronized_tracks: int = 0
    total_removed_tracks: int = 0
    exact_matches: int = 0
    high_matches: int = 0
    probable_matches: int = 0
    ambiguous_matches: int = 0
    unmatched_tracks: int = 0
    failed_tracks: int = 0
    manual_overrides_applied: int = 0
    duration_seconds: float = 0.0
    details: List[Dict[str, Any]] = Field(default_factory=list)
    unmatched_items: List[Dict[str, Any]] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
