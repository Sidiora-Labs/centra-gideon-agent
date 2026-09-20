import { useEffect, useRef, useState } from 'react'
import { api, ApiError, type SkillProposal, type SkillProposalDetail, type SkillSearchResult } from '../../shared/data/api'
import { useMutation } from '../../shared/data/data'

export function useSkillRequest(identity: string) {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const ticket = useRef<object | null>(null)
  useEffect(() => { ticket.current = null; setBusy(false); setErr(''); return () => { ticket.current = null } }, [identity])
  const run = async <Result,>(read: () => Promise<Result>, receive: (result: Result) => void, fallback: string) => {
    if (ticket.current) return
    const owner = {}; ticket.current = owner; setBusy(true); setErr('')
    try { const result = await read(); if (ticket.current === owner) receive(result) }
    catch (failure) { if (ticket.current === owner) setErr(failure instanceof Error ? failure.message || fallback : fallback) }
    finally { if (ticket.current === owner) { ticket.current = null; setBusy(false) } }
  }
  return { busy, err, setErr, run }
}
export function useSkillSearch(query: string, marketplace: string) {
  const [revision, setRevision] = useState(0)
  const [state, setState] = useState({ results: null as SkillSearchResult[] | null, counts: {} as Record<string, number>, installableSources: null as number | null, loading: false, searchErr: null as unknown })
  useEffect(() => {
    let current = true
    const term = query.trim()
    if (!term) { setState({ results: null, counts: {}, installableSources: null, loading: false, searchErr: null }); return }
    setState(previous => ({ ...previous, loading: true, searchErr: null }))
    const timer = setTimeout(() => {
      api.searchSkillsCounted(term, marketplace || undefined).then(result => {
        if (current) setState({ results: result.results, counts: result.counts, installableSources: result.installableSources, loading: false, searchErr: null })
      }).catch(failure => { if (current) setState({ results: null, counts: {}, installableSources: null, loading: false, searchErr: failure }) })
    }, 300)
    return () => { current = false; clearTimeout(timer) }
  }, [query, marketplace, revision])
  return { ...state, search: () => setRevision(value => value + 1) }
}
export function useProposalReview(proposal: SkillProposal) {
  const [open, setOpen] = useState(false)
  const [detail, setDetail] = useState<SkillProposalDetail | null>(null)
  const [done, setDone] = useState('')
  const [busy, setBusy] = useState('')
  const [loadError, setLoadError] = useState<unknown>(null)
  const [revision, setRevision] = useState(0)
  const decisionError = (failure: unknown) => setDone(failure instanceof ApiError && (failure.status === 404 || failure.status === 409) ? 'Already answered' : failure instanceof Error ? failure.message : 'Failed')
  const accepting = useMutation({ run: () => api.acceptSkillProposal(proposal.id), invalidates: [{ prefix: 'skill-proposals' }, 'skills'], onSuccess: result => setDone(['Accepted → ' + result.name, result.version ? `refinement v${result.version}` : ''].filter(Boolean).join(' · ')), onError: decisionError })
  const rejecting = useMutation({ run: () => api.rejectSkillProposal(proposal.id), invalidates: [{ prefix: 'skill-proposals' }], onSuccess: () => setDone('Rejected'), onError: decisionError })
  const lock = useRef(false)
  useEffect(() => {
    if (!open || detail) return
    let current = true
    setLoadError(null)
    api.skillProposalDetail(proposal.id).then(value => { if (current) setDetail(value) }).catch(failure => { if (current) setLoadError(failure) })
    return () => { current = false }
  }, [open, proposal.id, revision, detail])
  const decide = async (choice: 'accept' | 'reject') => {
    if (lock.current) return
    lock.current = true; setBusy(choice)
    try { await (choice === 'accept' ? accepting : rejecting).mutate() }
    finally { lock.current = false; setBusy('') }
  }
  return { open, detail, done, busy, loadError, expand: () => setOpen(value => !value), retry: () => setRevision(value => value + 1), accept: () => decide('accept'), reject: () => decide('reject') }
}
export function matchesSkill(query: string) {
  const needle = query.trim().toLowerCase()
  return (skill: { name: string; description?: string }) => !needle || [skill.name, skill.description ?? ''].join(' ').toLowerCase().includes(needle)
}
