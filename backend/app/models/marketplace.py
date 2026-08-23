from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.core.database import Base


class TrackedKeyword(Base):
    __tablename__ = "tracked_keywords"
    __table_args__ = (
        UniqueConstraint("user_id", "keyword", name="uq_tracked_keyword_user_term"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    keyword = Column(String(200), nullable=False, index=True)
    platforms = Column(JSON, nullable=False, default=lambda: ["shopee", "lazada"])
    results_limit = Column(Integer, nullable=False, default=20)
    tracking_enabled = Column(Boolean, nullable=False, default=True)
    daily_time = Column(String(5), nullable=False, default="20:00")
    timezone = Column(String(64), nullable=False, default="Asia/Kuala_Lumpur")
    last_run_at = Column(DateTime, nullable=True)
    last_success_at = Column(DateTime, nullable=True)
    next_run_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    user = relationship("User", back_populates="tracked_keywords")
    runs = relationship("AnalysisRun", back_populates="tracked_keyword", cascade="all, delete-orphan")
    snapshots = relationship("ListingSnapshot", back_populates="tracked_keyword", cascade="all, delete-orphan")


class AnalysisRun(Base):
    __tablename__ = "analysis_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    keyword_id = Column(Integer, ForeignKey("tracked_keywords.id", ondelete="CASCADE"), nullable=False, index=True)
    trigger = Column(String(20), nullable=False, default="manual")
    status = Column(String(32), nullable=False, default="pending", index=True)
    progress = Column(Integer, nullable=False, default=0)
    current_step = Column(String(100), nullable=True)
    verification_platform = Column(String(20), nullable=True)
    opportunity_score = Column(Float, nullable=True)
    verdict = Column(String(30), nullable=True)
    confidence = Column(Float, nullable=True)
    platform_scores = Column(JSON, nullable=True)
    analysis = Column(JSON, nullable=True)
    error_message = Column(Text, nullable=True)
    worker_id = Column(String(120), nullable=True, index=True)
    heartbeat_at = Column(DateTime, nullable=True, index=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    tracked_keyword = relationship("TrackedKeyword", back_populates="runs")
    snapshots = relationship("ListingSnapshot", back_populates="run", cascade="all, delete-orphan")


class ListingSnapshot(Base):
    __tablename__ = "listing_snapshots"
    __table_args__ = (
        UniqueConstraint("run_id", "platform", "item_id", name="uq_snapshot_run_platform_item"),
        Index("ix_snapshot_history", "keyword_id", "platform", "item_id", "collected_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(Integer, ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    keyword_id = Column(Integer, ForeignKey("tracked_keywords.id", ondelete="CASCADE"), nullable=False, index=True)
    platform = Column(String(20), nullable=False, index=True)
    item_id = Column(String(100), nullable=False, index=True)
    shop_id = Column(String(100), nullable=True)
    title = Column(String(1000), nullable=False)
    product_url = Column(String(1500), nullable=False)
    image_url = Column(String(1500), nullable=True)
    price = Column(Float, nullable=True)
    original_price = Column(Float, nullable=True)
    discount_percent = Column(Float, nullable=True)
    sold_count = Column(Integer, nullable=True)
    rating = Column(Float, nullable=True)
    review_count = Column(Integer, nullable=True)
    seller_name = Column(String(300), nullable=True)
    seller_location = Column(String(200), nullable=True)
    is_sponsored = Column(Boolean, nullable=True)
    search_rank = Column(Integer, nullable=False)
    data_quality = Column(Float, nullable=False, default=0)
    raw_data = Column(JSON, nullable=True)
    collected_at = Column(DateTime, server_default=func.now(), nullable=False)

    run = relationship("AnalysisRun", back_populates="snapshots")
    tracked_keyword = relationship("TrackedKeyword", back_populates="snapshots")
