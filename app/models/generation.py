from datetime import datetime, timezone
from sqlalchemy import Text, Float, String, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Generation(Base):
    __tablename__ = "generations"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    confidence_score: Mapped[float] = mapped_column(Float, nullable=True)
    result_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )
