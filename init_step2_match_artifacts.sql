create or replace function match_artifacts (
  query_embedding vector(1536),
  match_threshold float,
  match_count int
)
returns table (
  id uuid,
  meeting_id uuid,
  artifact_type text,
  content text,
  confidence float,
  similarity float
)
language sql stable
as $func$
  select
    artifacts.id,
    artifacts.meeting_id,
    artifacts.artifact_type,
    artifacts.content,
    artifacts.confidence,
    1 - (artifacts.embedding <=> query_embedding) as similarity
  from public.artifacts
  where (artifacts.embedding <=> query_embedding) < (1 - match_threshold)
  order by artifacts.embedding <=> query_embedding
  limit match_count;
$func$;
