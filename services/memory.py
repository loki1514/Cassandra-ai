from openai import OpenAI
from infrastructure.database import get_supabase
from config import config
from schemas.models import ArtifactType

client = OpenAI(api_key=config.OPENAI_API_KEY)
supabase = get_supabase()

def generate_embedding(text: str) -> list[float]:
    """Generates embedding vector for storing institutional memory."""
    # Using small fast model for < 300ms embedding processing
    response = client.embeddings.create(
        model="text-embedding-3-small",
        input=text
    )
    return response.data[0].embedding

def store_meeting_insight(meeting_id: str, artifact_type: ArtifactType, content: str, confidence: float):
    """Generates embeddings and stores the artifact to Supabase."""
    embedding = generate_embedding(content)
    
    data = {
        "meeting_id": meeting_id,
        "artifact_type": artifact_type.value,
        "content": content,
        "confidence": confidence,
        "embedding": embedding
    }
    
    response = supabase.table("artifacts").insert(data).execute()
    return response.data

def create_meeting(title: str = "Meeting Session"):
    """Creates a new meeting session."""
    data = {"title": title}
    response = supabase.table("meetings").insert(data).execute()
    return response.data[0]
