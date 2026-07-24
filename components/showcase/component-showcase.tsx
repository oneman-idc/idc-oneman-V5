'use client'

import {
  Heading,
  Text,
  Stack,
  Button,
  IconButton,
  Label,
  CounterLabel,
} from '@primer/react'
import {
  PlusIcon,
  DownloadIcon,
  TrashIcon,
  GitBranchIcon,
  HeartIcon,
} from '@primer/octicons-react'

export function ComponentShowcase() {
  return (
    <Stack direction="vertical" gap="normal">
      <Stack direction="vertical" gap="condensed">
        <Heading as="h2" variant="medium">
          Components
        </Heading>
        <Text size="medium" style={{ color: 'var(--fgColor-muted)' }}>
          Signature Primer controls in their real variants.
        </Text>
      </Stack>

      <Stack direction="vertical" gap="condensed">
        <Text
          size="small"
          weight="semibold"
          style={{ color: 'var(--fgColor-muted)' }}
        >
          Buttons
        </Text>
        <Stack
          direction="horizontal"
          gap="condensed"
          wrap="wrap"
          align="center"
        >
          <Button variant="primary" leadingVisual={PlusIcon}>
            New repository
          </Button>
          <Button variant="default" leadingVisual={DownloadIcon}>
            Clone
          </Button>
          <Button variant="danger" leadingVisual={TrashIcon}>
            Delete
          </Button>
          <Button variant="invisible">Cancel</Button>
          <IconButton icon={HeartIcon} aria-label="Star" variant="default" />
        </Stack>
      </Stack>

      <Stack direction="vertical" gap="condensed">
        <Text
          size="small"
          weight="semibold"
          style={{ color: 'var(--fgColor-muted)' }}
        >
          Labels &amp; counters
        </Text>
        <Stack
          direction="horizontal"
          gap="condensed"
          wrap="wrap"
          align="center"
        >
          <Label variant="accent">enhancement</Label>
          <Label variant="success">approved</Label>
          <Label variant="attention">needs review</Label>
          <Label variant="danger">bug</Label>
          <Label variant="done">resolved</Label>
          <Stack direction="horizontal" gap="none" align="center">
            <GitBranchIcon size={16} />
            <Text size="small" style={{ marginLeft: 4 }}>
              main
            </Text>
          </Stack>
          <CounterLabel>12</CounterLabel>
          <CounterLabel scheme="primary">3</CounterLabel>
        </Stack>
      </Stack>
    </Stack>
  )
}
