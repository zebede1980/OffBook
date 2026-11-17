export interface Profile {
  profile_id: number;
  name: string;
  colour: string;
  patience: number;
}

export interface Me {
  profile: Profile | null;
}

export type Kind = 'dialogue' | 'direction' | 'heading';

export interface ScriptSummary {
  script_id: number;
  title: string;
  created_at: string;
  pages: number;
  reading: number;
  failed: number;
  speeches: number;
  characters: number;
}

export interface Page {
  page_id: number;
  seq: number;
  status: 'queued' | 'done' | 'failed';
  error: string | null;
  printed_number: number | null;
}

export interface Character {
  character_id: number;
  name: string;
  lines: number;
  voice: string | null;
  speed: number;
}

export interface Voice {
  id: string;
  label: string;
  group: string;
}

export interface Line {
  line_id: number;
  seq: number;
  kind: Kind;
  text: string;
  spoken: string | null;
  speakers: number[];
  highlighted: boolean;
  unsure: string[];
  page_id: number | null;
}

export interface ScriptDetail {
  script: { script_id: number; title: string; created_at: string };
  pages: Page[];
  characters: Character[];
  lines: Line[];
}

export interface RehearsalLine {
  line_id: number;
  seq: number;
  kind: Kind;
  text: string;
  spoken: string | null;
  speakers: number[];
  mine: boolean;
  clip: string | null;
  clip_status: 'queued' | 'done' | 'failed' | null;
}

export interface Rehearsal {
  script: { script_id: number; title: string };
  mine: number[];
  characters: { character_id: number; name: string; voice: string | null }[];
  lines: RehearsalLine[];
  audio: { total: number; ready: number; failed: number };
}
