create or replace function match_transcripts (
  query_embedding vector(1536),
  match_threshold float,
  match_count int
)
returns table (
  id uuid,
  meeting_id uuid,
  chunk_index int,
  speaker_role text,
  content text,
  created_at timestamp with time zone,
  similarity float
)
language sql stable
as $func$
  select
    transcripts.id,
    transcripts.meeting_id,
    transcripts.chunk_index,
    transcripts.speaker_role,
    transcripts.content,
    transcripts.created_at,
    1 - (transcripts.embedding <=> query_embedding) as similarity
  from public.transcripts
  where (transcripts.embedding <=> query_embedding) < (1 - match_threshold)
  order by transcripts.embedding <=> query_embedding
  limit match_count;
$func$;
