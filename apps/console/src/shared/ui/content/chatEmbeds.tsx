import { createElement, memo } from 'react'
import type { EmbedProps } from './contentTypes'
import { WidgetFrame } from '../widget/WidgetFrame'
import { ReactWidgetFrame } from '../widget/ReactWidgetFrame'

export const HtmlWidgetEmbed = memo(function HtmlWidgetEmbed(props: EmbedProps) {
  const { content, ...identity } = props
  return createElement(WidgetFrame, { ...identity, html: content })
})
export const ReactWidgetEmbed = memo(function ReactWidgetEmbed(props: EmbedProps) {
  return createElement(ReactWidgetFrame, { jsx: props.content, title: props.title })
})
