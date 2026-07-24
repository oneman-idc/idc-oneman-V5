'use client'

import {
  Heading,
  Text,
  Stack,
  FormControl,
  TextInput,
  Textarea,
  Select,
  Checkbox,
  Button,
  Flash,
  Avatar,
} from '@primer/react'
import { RepoIcon } from '@primer/octicons-react'

export function CreateRepoCard() {
  return (
    <Stack direction="vertical" gap="normal">
      <Stack direction="vertical" gap="condensed">
        <Heading as="h2" variant="medium">
          Composition
        </Heading>
        <Text size="medium" style={{ color: 'var(--fgColor-muted)' }}>
          A realistic form built from Primer primitives.
        </Text>
      </Stack>

      <div
        style={{
          backgroundColor: 'var(--bgColor-default)',
          border: '1px solid var(--borderColor-default)',
          borderRadius: 'var(--borderRadius-large, 12px)',
          boxShadow: 'var(--shadow-resting-medium)',
        }}
      >
        <Stack direction="vertical" gap="normal" padding="normal">
          <Stack direction="horizontal" gap="condensed" align="center">
            <Avatar
              src="https://avatars.githubusercontent.com/u/9919?v=4"
              size={40}
            />
            <Stack direction="vertical" gap="none">
              <Stack direction="horizontal" gap="condensed" align="center">
                <RepoIcon size={16} />
                <Heading as="h3" variant="small">
                  Create a new repository
                </Heading>
              </Stack>
              <Text size="small" style={{ color: 'var(--fgColor-muted)' }}>
                A repository contains all project files and revision history.
              </Text>
            </Stack>
          </Stack>

          <Flash variant="default">
            You are creating this repository in your personal account.
          </Flash>

          <FormControl required>
            <FormControl.Label>Repository name</FormControl.Label>
            <TextInput
              block
              placeholder="awesome-project"
              leadingVisual={RepoIcon}
            />
            <FormControl.Caption>
              Great repository names are short and memorable.
            </FormControl.Caption>
          </FormControl>

          <FormControl>
            <FormControl.Label>Description</FormControl.Label>
            <Textarea
              block
              rows={3}
              placeholder="Optional description"
              resize="vertical"
            />
          </FormControl>

          <FormControl>
            <FormControl.Label>Visibility</FormControl.Label>
            <Select>
              <Select.Option value="public">Public</Select.Option>
              <Select.Option value="private">Private</Select.Option>
            </Select>
          </FormControl>

          <FormControl>
            <Checkbox defaultChecked />
            <FormControl.Label>Add a README file</FormControl.Label>
            <FormControl.Caption>
              This is where you can write a long description for your project.
            </FormControl.Caption>
          </FormControl>

          <Stack direction="horizontal" gap="condensed" justify="end">
            <Button variant="invisible">Cancel</Button>
            <Button variant="primary">Create repository</Button>
          </Stack>
        </Stack>
      </div>
    </Stack>
  )
}
