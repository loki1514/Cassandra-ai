from infrastructure.database import get_supabase
from services.memory import generate_embedding
from services.pageindex import retrieve_page_context

supabase = get_supabase()

def search_relevant_artifacts(query: str, limit: int = 3) -> str:
    """Search structured artifacts (Decisions, Risks, Topics) via pgvector."""
    query_embedding = generate_embedding(query)
    
    try:
        result = supabase.rpc(
            'match_artifacts', 
            {'query_embedding': query_embedding, 'match_threshold': 0.78, 'match_count': limit}
        ).execute()
        
        artifacts = result.data
        if not artifacts:
            return ""
            
        context_blocks = []
        for row in artifacts:
            context_blocks.append(f"[{row['artifact_type'].upper()}] {row['content']} (Confidence: {row.get('confidence', 1.0)})")
            
        return "\n".join(context_blocks)
    except Exception as e:
        print(f"Artifact search failed: {e}")
        return ""

def search_all_context(query: str) -> str:
    """
    Combined Hybrid RAG: searches both structured artifacts AND raw transcript chunks.
    This uses the Artifact logic + the PageIndex Transcript service.
    """
    artifact_context = search_relevant_artifacts(query, limit=3)
    # Call the PageIndex tool
    transcript_context = retrieve_page_context(query, limit=5)
    
    sections = []
    
    if artifact_context:
        sections.append("=== INSTITUTIONAL MEMORY (Decisions & Risks) ===\n" + artifact_context)
    
    if transcript_context:
        sections.append("=== MEETING TRANSCRIPTS (via PageIndex) ===\n" + transcript_context)
    
    if not sections:
        return "No relevant context found in institutional memory."
    
    return "\n\n".join(sections)

