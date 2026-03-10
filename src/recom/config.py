from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Claude
    anthropic_api_key: str = ""
    claude_model: str = "claude-sonnet-4-20250514"

    # YouTube
    google_client_secrets_file: str = "state/tokens/client_secret.json"
    youtube_token_file: str = "state/tokens/youtube_token.json"
    gmail_token_file: str = "state/tokens/gmail_token.json"

    # Spotify
    spotify_client_id: str = ""
    spotify_client_secret: str = ""
    spotify_redirect_uri: str = "http://localhost:8888/callback"
    spotify_token_file: str = "state/tokens/spotify_cache"

    # Location
    location_query: str = "Cambridge, MA"
    zip_code: str = "02139"
    latitude: float = 42.3736
    longitude: float = -71.1097
    max_commute_minutes: int = 30

    # Event APIs (optional)
    eventbrite_token: str = ""
    songkick_api_key: str = ""
    ticketmaster_api_key: str = ""

    # Email
    email_to: str = ""
    email_from: str = ""
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""

    # Paths
    db_path: str = "recom.db"
    state_dir: str = "state"
    interests_file: str = "my_interests.txt"
    bucket_list_file: str = "bucket_list.txt"
    newsletter_senders_file: str = "newsletter_senders.txt"

    # Dashboard
    dashboard_host: str = "127.0.0.1"
    dashboard_port: int = 8000
    dashboard_url: str = "https://recom.arthgupta.dev"

    model_config = {"env_file": ".env", "env_prefix": "RECOM_"}


MODEL_PRICING: dict[str, dict[str, float]] = {
    "claude-sonnet-4-20250514": {"input": 3.0 / 1_000_000, "output": 15.0 / 1_000_000},
    "claude-sonnet-4-6": {"input": 3.0 / 1_000_000, "output": 15.0 / 1_000_000},
    "claude-haiku-4-5-20251001": {"input": 0.80 / 1_000_000, "output": 4.0 / 1_000_000},
    "claude-opus-4-6": {"input": 15.0 / 1_000_000, "output": 75.0 / 1_000_000},
}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    pricing = MODEL_PRICING.get(model, {"input": 3.0 / 1_000_000, "output": 15.0 / 1_000_000})
    return input_tokens * pricing["input"] + output_tokens * pricing["output"]
