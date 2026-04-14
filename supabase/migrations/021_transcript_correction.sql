-- Migration 021: Human Correction Loop for Transcript Speaker Correction
--
-- Adds correction tracking columns to enriched_transcripts so that when
-- Expo users correct a wrong speaker attribution, we record:
--   - Who made the correction
--   - When
--   - What the original match was (for audit/revert)
--
-- Also adds an index for fast "corrected segments" queries.

-- Add correction tracking columns
ALTER TABLE enriched_transcripts
    ADD COLUMN IF NOT EXISTS original_speaker_user_id UUID,
    ADD COLUMN IF NOT EXISTS corrected_by UUID REFERENCES users(id),
    ADD COLUMN IF NOT EXISTS corrected_at TIMESTAMPTZ;

COMMENT ON COLUMN enriched_transcripts.original_speaker_user_id IS
    'The speaker_user_id that was assigned before human correction. Used for audit trail and potential revert.';
COMMENT ON COLUMN enriched_transcripts.corrected_by IS
    'User ID who made the speaker correction in Expo.';
COMMENT ON COLUMN enriched_transcripts.corrected_at IS
    'Timestamp when the speaker correction was applied.';

-- Index for finding segments corrected by a specific user
CREATE INDEX IF NOT EXISTS idx_enriched_transcripts_corrected_by
    ON enriched_transcripts(corrected_by)
    WHERE corrected_by IS NOT NULL;

-- Index for finding segments not yet corrected
CREATE INDEX IF NOT EXISTS idx_enriched_transcripts_needs_correction
    ON enriched_transcripts(speaker_user_id)
    WHERE speaker_user_id IS NULL AND corrected_by IS NULL;
