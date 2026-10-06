import { describe, expect, it } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { Markdown } from './Markdown'

describe('home file citations', () => {
  it('opens home paths in prose and inline code without changing their spelling', () => {
    const opened: string[] = []
    render(<Markdown onFileClick={path => opened.push(path)}>
      {'Read ~/Notes/weekly.md and ~/todo.md, then `~/.gideon/memory.md`.'}
    </Markdown>)
    const paths = ['~/Notes/weekly.md', '~/todo.md', '~/.gideon/memory.md']
    paths.forEach(path => fireEvent.click(screen.getByRole('button', { name: path })))
    expect(opened).toEqual(paths)
    expect(screen.getAllByRole('button')).toHaveLength(paths.length)
  })

  it('keeps absolute and relative file links using the same native callback', () => {
    const opened: string[] = []
    render(<Markdown onFileClick={path => opened.push(path)}>
      {'See /srv/app/main.py, ../src/main.ts and `./README.md`.'}
    </Markdown>)
    const paths = ['/srv/app/main.py', '../src/main.ts', './README.md']
    paths.forEach(path => fireEvent.click(screen.getByRole('button', { name: path })))
    expect(opened).toEqual(paths)
  })

  it('does not reinterpret a named tilde prefix or fenced code as a home link', () => {
    render(<Markdown onFileClick={() => { throw new Error('unexpected file action') }}>
      {'Ask ~ada/notes.md or `~ada/notes.md`.\n\n```text\n~/todo.md\n```'}
    </Markdown>)
    expect(screen.queryByRole('button', { name: /notes\.md|~\/todo\.md/ })).toBeNull()
  })
})
