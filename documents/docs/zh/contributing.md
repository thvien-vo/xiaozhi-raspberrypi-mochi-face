---
title: Contributing Guide
description: How to contribute code to the py-xiaozhi project
sidebar: false
outline: deep
---

<div class="contributing-page">

# Contributing Guide

<div class="header-content">
  <h2>How to Contribute Code to the py-xiaozhi Project 🚀</h2>
</div>

## Introduction

Thank you for your interest in the py-xiaozhi project! We warmly welcome community members to contribute, whether fixing bugs, improving documentation, or adding new features. This guide will help you understand how to submit contributions to the project.

## Development Environment Setup

### Basic Requirements

- Python 3.9 or higher
- Git version control system
- Basic Python development tools (Visual Studio Code recommended)

### Getting the Source Code

1. First, Fork this project on GitHub to your own account:
   - Visit the [py-xiaozhi project page](https://github.com/huangjunsen0406/py-xiaozhi)
   - Click the "Fork" button in the top right corner
   - Wait for the fork to complete, and you will be redirected to your repository copy

2. Clone your forked repository locally:

```bash
git clone https://github.com/YOUR_USERNAME/py-xiaozhi.git
cd py-xiaozhi
```

3. Add the upstream repository as a remote source:

```bash
git remote add upstream https://github.com/huangjunsen0406/py-xiaozhi.git
```

You can use the `git remote -v` command to confirm that the remote repositories are properly configured:

```bash
git remote -v
# Should display:
# origin    https://github.com/YOUR_USERNAME/py-xiaozhi.git (fetch)
# origin    https://github.com/YOUR_USERNAME/py-xiaozhi.git (push)
# upstream  https://github.com/huangjunsen0406/py-xiaozhi.git (fetch)
# upstream  https://github.com/huangjunsen0406/py-xiaozhi.git (push)
```

### Installing Development Dependencies
- For other dependencies, please refer to the relevant documentation under the Guide section
```bash
# Create and activate a virtual environment (recommended)
python -m venv venv
source venv/bin/activate  # On Windows use: venv\Scripts\activate

# Install project dependencies
pip install -r requirements.txt
```

## Development Workflow

### Staying in Sync with the Main Repository

Before starting work, it is very important to ensure your local repository stays in sync with the main project. Here are the steps to sync your local repository:

1. Switch to your main branch (`main`):

```bash
git checkout main
```

2. Fetch the latest changes from the upstream repository:

```bash
git fetch upstream
```

3. Merge changes from the upstream main branch into your local main branch:

```bash
git merge upstream/main
```

4. Push the updated local main branch to your GitHub repository:

```bash
git push origin main
```

### Creating a Branch

Before starting any work, make sure to create a new branch from the latest upstream main branch:

```bash
# Fetch the latest upstream code (as described in the previous section)
git fetch upstream
git checkout -b feature/your-feature-name upstream/main
```

When naming your branch, you can follow these conventions:
- `feature/xxx`: New feature development
- `fix/xxx`: Bug fixes
- `docs/xxx`: Documentation updates
- `test/xxx`: Testing-related work
- `refactor/xxx`: Code refactoring

### Coding Standards

We use [PEP 8](https://www.python.org/dev/peps/pep-0008/) as our Python code style guide. Before submitting code, please ensure your code meets the following requirements:

- Use 4 spaces for indentation
- Line length should not exceed 120 characters
- Use meaningful variable and function names
- Add docstrings for public APIs
- Use Type Hints

We recommend using static code analysis tools to help you follow coding standards:

```bash
# Check code style with flake8
flake8 .

# Run type checking with mypy
mypy .
```

### Testing

Before submitting, please make sure all tests pass.

## Submitting Changes

### Pre-Submission Checklist

Before submitting your code, please ensure you complete the following checks:

1. Does the code conform to PEP 8 standards?
2. Have necessary test cases been added?
3. Do all tests pass?
4. Has appropriate documentation been added?
5. Does it solve the problem you intended to solve?
6. Is it in sync with the latest upstream code?

### Committing Changes

During development, form the habit of making small, frequent commits. This makes your changes easier to track and understand:

```bash
# View changed files
git status

# Stage changes
git add file1.py file2.py

# Commit changes
git commit -m "feat: add new feature X"
```

### Resolving Conflicts

If you encounter conflicts when trying to merge upstream changes, follow these steps to resolve them:

1. First identify where the conflicts are:

```bash
git status
```

2. Open the conflicted files, where you will see markers similar to the following:

```
<<<<<<< HEAD
Your code
=======
Upstream code
>>>>>>> upstream/main
```

3. Edit the file to resolve the conflict and remove the conflict markers
4. After resolving all conflicts, stage and commit:

```bash
git add .
git commit -m "fix: resolve merge conflicts"
```

### Commit Conventions

We use the [Conventional Commits](https://www.conventionalcommits.org/en/) specification to format Git commit messages. Commit messages should follow this format:

```
<type>[optional scope]: <description>

[optional body]

[optional footer]
```

Common commit types include:
- `feat`: A new feature
- `fix`: A bug fix
- `docs`: Documentation only changes
- `style`: Changes that do not affect the meaning of the code (white-space, formatting, etc.)
- `refactor`: A code change that neither fixes a bug nor adds a feature
- `perf`: A code change that improves performance
- `test`: Adding missing tests or correcting existing tests
- `chore`: Changes to the build process or auxiliary tools and libraries

For example:

```
feat(tts): add support for new speech synthesis engine

Add support for Baidu speech synthesis API, including the following features:
- Support multiple voice options
- Support speech rate and volume adjustment
- Support mixed Chinese and English synthesis

Fixes #123
```

### Pushing Changes

After completing your code changes, push your branch to your GitHub repository:

```bash
git push origin feature/your-feature-name
```

If you have already created a Pull Request and need to update it, simply push to the same branch again:

```bash
# After making additional changes
git add .
git commit -m "refactor: improve code based on feedback"
git push origin feature/your-feature-name
```

### Syncing the Latest Code Before Creating a Pull Request

Before creating a Pull Request, it is recommended to sync with the upstream repository again to avoid potential conflicts:

```bash
# Fetch the latest upstream code
git fetch upstream

# Rebase your feature branch onto the latest upstream code
git rebase upstream/main

# If conflicts occur, resolve them and continue rebasing
git add .
git rebase --continue

# Force push the updated branch to your repository
git push --force-with-lease origin feature/your-feature-name
```

Note: Using `--force-with-lease` is safer than using `--force` directly, as it prevents overwriting changes pushed by others.

### Creating a Pull Request

When you finish developing a feature or fixing an issue, follow these steps to create a Pull Request:

1. Push your changes to GitHub:

```bash
git push origin feature/your-feature-name
```

2. Visit your forked repository page on GitHub and click the "Compare & pull request" button

3. Fill out the Pull Request form:
   - Use a clear title following the commit message format
   - Provide detailed information in the description
   - Reference related issues (using the `#issue_number` format)
   - If this is a work in progress, add the `[WIP]` prefix to the title

4. Submit the Pull Request and wait for project maintainers to review it

### Pull Request Lifecycle

1. **Created**: Submit your PR
2. **CI Checks**: Automated tests and code style checks
3. **Code Review**: Maintainers review your code and provide feedback
4. **Revision**: Update your code based on feedback
5. **Approved**: Once your PR is approved
6. **Merged**: Maintainers merge your PR into the main branch

## Documentation Contributions

If you want to improve project documentation, follow these steps:

1. Fork the project and clone it locally as described above

2. Documentation is located in the `documents/docs` directory in Markdown format

3. Install documentation development dependencies:

```bash
cd documents
pnpm install
```

4. Start the local documentation server:

```bash
pnpm docs:dev
```

5. Visit `http://localhost:5173/py-xiaozhi/` in your browser to preview your changes

6. After completing your changes, commit your contribution and create a Pull Request

### Documentation Writing Guidelines

- Use clear and concise language
- Provide practical examples
- Explain complex concepts in detail
- Include appropriate screenshots or diagrams when needed
- Avoid excessive technical jargon; provide explanations when necessary
- Keep documentation structure consistent

## Issue Reporting

If you find an issue that you cannot fix right away, please [create an Issue](https://github.com/huangjunsen0406/py-xiaozhi/issues/new) on GitHub. When creating an Issue, please include the following information:

- Detailed description of the problem
- Steps to reproduce the problem
- Expected behavior and actual behavior
- Your operating system and Python version
- Relevant log output or error messages

## Code Review

After submitting a Pull Request, project maintainers will review your code. During the code review process:

- Please wait patiently for feedback
- Respond promptly to comments and suggestions
- Make necessary changes and update your Pull Request when needed
- Keep discussions polite and constructive

### Handling Code Review Feedback

1. Read all comments and suggestions carefully
2. Respond to or make changes for each point
3. If you disagree with a suggestion, politely explain your reasoning
4. Once modifications are complete, leave a comment on the PR to notify the reviewers

## Becoming a Project Maintainer

If you consistently make valuable contributions to the project, you may be invited to become a project maintainer. As a maintainer, you will have permission to review and merge Pull Requests from others.

### Maintainer Responsibilities

- Review Pull Requests
- Manage issues
- Participate in project planning
- Answer community questions
- Help onboard new contributors

## Code of Conduct

Please respect all project participants and follow this Code of Conduct:

- Use inclusive language
- Respect differing viewpoints and experiences
- Gracefully accept constructive criticism
- Focus on what is best for the community
- Show empathy towards other community members

## Frequently Asked Questions

### Where should I start contributing?

1. Look for issues labeled "good first issue"
2. Fix errors or unclear sections in the documentation
3. Add more test cases
4. Solve issues you encounter while using the project yourself

### My PR hasn't received a response for a long time. What should I do?

Leave a comment on the PR politely asking if any further improvements or clarifications are needed. Please understand that maintainers may be busy and need some time to review your contribution.

### What types of changes can I contribute?

- Bug fixes
- New features
- Performance improvements
- Documentation updates
- Test cases
- Code refactoring

## Acknowledgements

Thank you once again for contributing to the project! Your participation means a lot to us as we work together to make py-xiaozhi even better!

</div>

<style>
.contributing-page {
  max-width: 900px;
  margin: 0 auto;
  padding: 2rem 1.5rem;
}

.contributing-page h1 {
  text-align: center;
  margin-bottom: 1rem;
}

.header-content {
  text-align: center;
}

.header-content h2 {
  color: var(--vp-c-brand);
  margin-bottom: 1rem;
}

.contributing-page h2 {
  margin-top: 3rem;
  padding-top: 1rem;
  border-top: 1px solid var(--vp-c-divider);
}

.contributing-page h3 {
  margin-top: 2rem;
}

.contributing-page code {
  background-color: var(--vp-c-bg-soft);
  padding: 0.2em 0.4em;
  border-radius: 3px;
}

.contributing-page pre {
  margin: 1rem 0;
  border-radius: 8px;
  overflow: auto;
}
</style>
