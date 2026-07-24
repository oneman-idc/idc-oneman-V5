'use client'

import { Heading, Text, Stack } from '@primer/react'

export function TypeScale() {
  return (
    <Stack direction="vertical" gap="normal">
      <Stack direction="vertical" gap="condensed">
        <Heading as="h2" variant="medium">
          Typography
        </Heading>
        <Text size="medium" style={{ color: 'var(--fgColor-muted)' }}>
          Headings and body text scale via Primer&apos;s type tokens and the
          Heading / Text components.
        </Text>
      </Stack>

      <Stack direction="vertical" gap="normal">
        <Stack direction="vertical" gap="none">
          <Heading as="h1" variant="large">
            Display heading
          </Heading>
          <Text size="small" style={{ color: 'var(--fgColor-muted)' }}>
            Heading · variant=&quot;large&quot;
          </Text>
        </Stack>

        <Stack direction="vertical" gap="none">
          <Heading as="h2" variant="medium">
            Section heading
          </Heading>
          <Text size="small" style={{ color: 'var(--fgColor-muted)' }}>
            Heading · variant=&quot;medium&quot;
          </Text>
        </Stack>

        <Stack direction="vertical" gap="none">
          <Heading as="h3" variant="small">
            Subsection heading
          </Heading>
          <Text size="small" style={{ color: 'var(--fgColor-muted)' }}>
            Heading · variant=&quot;small&quot;
          </Text>
        </Stack>

        <Stack direction="vertical" gap="none">
          <Text size="large">Large body — used for emphasis and intros.</Text>
          <Text size="medium">
            Medium body — the default reading size for product UI content.
          </Text>
          <Text size="small" style={{ color: 'var(--fgColor-muted)' }}>
            Small body — captions and secondary metadata.
          </Text>
        </Stack>
      </Stack>
    </Stack>
  )
}
