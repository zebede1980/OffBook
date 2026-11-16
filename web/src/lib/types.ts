export interface Profile {
  profile_id: number;
  name: string;
  colour: string;
  patience: number;
}

export interface Me {
  profile: Profile | null;
}
