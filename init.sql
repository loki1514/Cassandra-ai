-- Enable the pgvector extension to work with embedding vectors
create extension if not exists vector;

-- Create meetings table
create table if not exists public.meetings (
  id uuid default gen_random_uuid() primary key,
  title text,
  created_at timestamp with time zone default timezone('utc'::text, now()) not null
);

-- Create transcripts table (PageIndex chunks with vector embeddings)
create table if not exists public.transcripts (
  id uuid default gen_random_uuid() primary key,
  meeting_id uuid references public.meetings(id) on delete cascade,
  chunk_index int not null default 0,
  speaker_role text not null default 'user',
  content text not null,
  embedding vector(1536),
  created_at timestamp with time zone default timezone('utc'::text, now()) not null
);

-- Create artifacts table to store extracted insights
create table if not exists public.artifacts (
  id uuid default gen_random_uuid() primary key,
  meeting_id uuid references public.meetings(id) on delete cascade,
  artifact_type text check (artifact_type in ('decision', 'risk', 'topic', 'summary')),
  content text not null,
  confidence float,
  embedding vector(1536),
  created_at timestamp with time zone default timezone('utc'::text, now()) not null
);

-- HNSW indexes for fast vector searches
create index if not exists artifacts_embedding_idx on public.artifacts using hnsw (embedding vector_cosine_ops);
create index if not exists transcripts_embedding_idx on public.transcripts using hnsw (embedding vector_cosine_ops);

-- RPC: semantic search over artifacts
drop function if exists public.match_artifacts(vector, float, int);
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
  where 1 - (artifacts.embedding <=> query_embedding) > match_threshold
  order by artifacts.embedding <=> query_embedding
  limit match_count;
$func$;

-- RPC: semantic search over transcript chunks (PageIndex)
drop function if exists public.match_transcripts(vector, float, int);
create or replace function match_transcripts (
  query_embedding vector(1536),
  match_threshold float,
  match_count int
)
returns table (
  id uuid,
  meeting_id uuid,
  speaker_role text,
  content text,
  similarity float
)
language sql stable
as $func$
  select
    transcripts.id,
    transcripts.meeting_id,
    transcripts.speaker_role,
    transcripts.content,
    1 - (transcripts.embedding <=> query_embedding) as similarity
  from public.transcripts
  where 1 - (transcripts.embedding <=> query_embedding) > match_threshold
  order by transcripts.embedding <=> query_embedding
  limit match_count;
$func$;
