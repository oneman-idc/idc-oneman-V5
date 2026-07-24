'use client'

import { Heading, Text, Stack } from '@primer/react'

const TOKENS: { label: string; token: string }[] = [
  { label: 'Accent', token: '--bgColor-accent-emphasis' },
  { label: 'Success', token: '--bgColor-success-emphasis' },
  { label: 'Attention', token: '--bgColor-attention-emphasis' },
  { label: 'Severe', token: '--bgColor-severe-emphasis' },
  { label: 'Danger', token: '--bgColor-danger-emphasis' },
  { label: 'Done', token: '--bgColor-done-emphasis' },
  { label: 'Sponsors', token: '--bgColor-sponsors-emphasis' },
  { label: 'Neutral', token: '--bgColor-neutral-emphasis' },
]

const SURFACES: { label: string; token: string }[] = [
  { label: 'Default', token: '--bgColor-default' },
  { label: 'Muted', token: '--bgColor-muted' },
  { label: 'Inset', token: '--bgColor-inset' },
  { label: 'Emphasis', token: '--bgColor-emphasis' },
]

export function ColorPalette() {
  return (
    <Stack direction="vertical" gap="normal">
      <Stack direction="vertical" gap="condensed">
        <Heading as="h2" variant="medium">
          Color
        </Heading>
        <Text size="medium" style={{ color: 'var(--fgColor-muted)' }}>
          Semantic role tokens from <code>@primer/primitives</code>. Never
          hardcode hex values.
        </Text>
      </Stack>

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))',
          gap: 'var(--stack-gap-condensed, 12px)',
        }}
      >
        {TOKENS.map(({ label, token }) => (
          <div
            key={token}
            style={{
              borderRadius: 'var(--borderRadius-medium)',
              overflow: 'hidden',
              border:
                'var(--borderWidth-thin) solid var(--borderColor-default)',
            }}
          >
            <div style={{ height: 56, backgroundColor: `var(${token})` }} />
            <div
              style={{
                padding: '8px 12px',
                backgroundColor: 'var(--bgColor-default)',
              }}
            >
              <Text size="small" weight="semibold" as="div">
                {label}
              </Text>
              <Text
                size="small"
                as="div"
                style={{ color: 'var(--fgColor-muted)' }}
              >
                {token.replace('--bgColor-', '')}
              </Text>
            </div>
          </div>
        ))}
      </div>

      <Stack direction="horizontal" gap="condensed" wrap="wrap">
        {SURFACES.map(({ label, token }) => (
          <div
            key={token}
            style={{
              flex: '1 1 140px',
              padding: '16px',
              borderRadius: 'var(--borderRadius-medium)',
              border:
                'var(--borderWidth-thin) solid var(--borderColor-default)',
              backgroundColor: `var(${token})`,
            }}
          >
            <Text
              size="small"
              weight="semibold"
              style={{
                color:
                  token === '--bgColor-emphasis'
                    ? 'var(--fgColor-onEmphasis)'
                    : 'var(--fgColor-default)',
              }}
            >
              {label}
            </Text>
          </div>
        ))}
      </Stack>
    </Stack>
  )
}
