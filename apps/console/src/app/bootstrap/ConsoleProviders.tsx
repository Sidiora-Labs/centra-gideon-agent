import { StrictMode, createElement, type ComponentType, type ReactNode } from 'react'
import { AppearanceProvider } from '../shell/appearance'
import { IdentityProvider } from '../shell/identity'
import { PersonalityProvider } from '../shell/personality'
import { ThemeProvider } from '../shell/theme'

type Provider = ComponentType<{ children: ReactNode }>
const providers: readonly Provider[] = [ThemeProvider, AppearanceProvider, PersonalityProvider, IdentityProvider]

export function ConsoleProviders({ children }: { children: ReactNode }) {
  const tree = providers.reduceRight<ReactNode>((content, Provider) => createElement(Provider, { children: content }), children)
  return <StrictMode>{tree}</StrictMode>
}
