import { createContext, useContext } from 'react'

/** "real" when the backend runs on real data; "mock" when it serves sample data.
 *  AI action buttons only show in real mode (the backend refuses them in mock mode). */
export type Mode = 'real' | 'mock' | 'unknown'

export const ModeContext = createContext<Mode>('unknown')

export const useMode = () => useContext(ModeContext)
export const useIsReal = () => useContext(ModeContext) === 'real'
