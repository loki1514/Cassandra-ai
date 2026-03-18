ALTER TABLE public.transcripts ADD COLUMN IF NOT EXISTS chunk_index int NOT NULL DEFAULT 0;
ALTER TABLE public.transcripts ADD COLUMN IF NOT EXISTS speaker_role text NOT NULL DEFAULT 'user';
ALTER TABLE public.transcripts ADD COLUMN IF NOT EXISTS embedding vector(1536);

DO $$ 
BEGIN 
  IF EXISTS (SELECT 1 FROM information_schema.columns 
             WHERE table_name='transcripts' AND column_name='text') THEN
    ALTER TABLE public.transcripts RENAME COLUMN text TO content;
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS transcripts_embedding_idx 
ON public.transcripts USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS artifacts_embedding_idx 
ON public.artifacts USING hnsw (embedding vector_cosine_ops);
