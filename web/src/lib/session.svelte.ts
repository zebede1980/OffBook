import type { Profile } from './types';

export const session = $state<{ profile: Profile | null }>({ profile: null });
