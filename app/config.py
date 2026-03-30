from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    OPENROUTER_API_KEY: str
    # Model ID as used by OpenRouter, e.g.:
    #   anthropic/claude-sonnet-4-5
    #   openai/gpt-4o
    #   openai/gpt-4-turbo
    #   meta-llama/llama-3.1-70b-instruct
    AI_MODEL: str = "openai/gpt-4.5"
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"

    # TTS provider — "edge" (default, free) | "minimax" | "openai" | "elevenlabs"
    TTS_PROVIDER: str = "edge"
    EDGE_TTS_VOICE: str = "en-US-AriaNeural"

    # MiniMax TTS
    MINIMAX_API_KEY: str = ""
    MINIMAX_TTS_MODEL: str = "speech-02-hd"
    MINIMAX_TTS_VOICE: str = "Calm_Woman"
    MINIMAX_API_BASE: str = "https://api.minimaxi.chat/v1"

    # OpenAI TTS
    OPENAI_API_KEY: str = ""
    OPENAI_TTS_MODEL: str = "tts-1-hd"
    OPENAI_TTS_VOICE: str = "nova"

    # ElevenLabs TTS (fallback / legacy)
    ELEVENLABS_API_KEY: str = ""
    ELEVENLABS_MODEL: str = "eleven_multilingual_v2"
    ELEVENLABS_DEFAULT_VOICE: str = "Rachel"

    # Supabase
    SUPABASE_URL: str = ""
    SUPABASE_SECRET_KEY: str = ""

    # Cloudinary
    CLOUDINARY_CLOUD_NAME: str = ""
    CLOUDINARY_API_KEY: str = ""
    CLOUDINARY_API_SECRET: str = ""

    # Video rendering
    VIDEOS_DIR: str = "videos"
    SANDBOX_TIMEOUT: int = 10
    MAX_ARRAY_SIZE: int = 64
    LOG_LEVEL: str = "INFO"
    ENV: str = "development"
    DATABASE_URL: str = "sqlite+aiosqlite:///./algo_visuals.db"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
