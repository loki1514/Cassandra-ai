from infrastructure.database import get_supabase
from services.memory import generate_embedding

supabase = get_supabase()

def index_transcript_chunk(meeting_id: str, speaker_role: str, content: str, chunk_index: int):
    """
    The PageIndex 'Writer': Generates embeddings and indexes a conversation chunk.
    """
    embedding = generate_embedding(content)
    data = {
        "meeting_id": meeting_id,
        "speaker_role": speaker_role,
        "content": content,
        "chunk_index": chunk_index,
        "embedding": embedding
    }
    return supabase.table("transcripts").insert(data).execute()

def retrieve_page_context(query: str, limit: int = 5) -> str:
    """
    The PageIndex 'Reader': Performs semantic retrieval and rehydrates context.
    Includes chronological reconstruction.
    """
    query_embedding = generate_embedding(query)
    
    try:
        # 1. Semantic Pull
        result = supabase.rpc(
            'match_transcripts',
            {'query_embedding': query_embedding, 'match_threshold': 0.78, 'match_count': limit}
        ).execute()
        
        base_hits = result.data
        if not base_hits:
            return ""
        
        # 2. Context Rehydration (Neighbors)
        rehydrated_ids = {t['id'] for t in base_hits}
        all_chunks = list(base_hits)
        
        for hit in base_hits:
            if hit.get('similarity', 0) > 0.85:
                m_id = hit['meeting_id']
                idx = hit['chunk_index']
                
                neighbors = supabase.table("transcripts") \
                    .select("id, meeting_id, chunk_index, speaker_role, content, created_at") \
                    .filter("meeting_id", "eq", m_id) \
                    .in_("chunk_index", [idx-1, idx+1]) \
                    .execute()
                
                for n in neighbors.data:
                    if n['id'] not in rehydrated_ids:
                        all_chunks.append(n)
                        rehydrated_ids.add(n['id'])
        
        # 3. Chronological Sort
        all_chunks.sort(key=lambda x: x.get('chunk_index', 0))
        
        context_blocks = []
        for row in all_chunks:
            # Format timestamp nicely if exists
            timestamp = row.get('created_at', '')
            if timestamp:
                timestamp = f" ({timestamp[:19]})" # Grab just the YYYY-MM-DD HH:MM:SS part
            
            context_blocks.append(f"[{row['speaker_role'].upper()}{timestamp}] {row['content']}")
            
        return "\n".join(context_blocks)
    except Exception as e:
        print(f"PageIndex retrieval failed: {e}")
        return ""
