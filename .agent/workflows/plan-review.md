---
description: Systematic plan review and improvement process
---

# Plan Review Workflow

This workflow ensures thorough review of implementation plans before execution.

## Steps

### 1. Compliance Check
- [ ] Verify plan follows TopstepX rules (MLL, DLL, flatten time, position limits)
- [ ] Check for prohibited practices (overnight positions, exceeding contract limits)
- [ ] Ensure risk parameters are within account limits

### 2. Research Best Practices
- [ ] Search for industry best practices for each feature
- [ ] Check for common pitfalls and failure modes
- [ ] Review similar implementations in open source

### 3. Iterative Review (5x)
For each iteration:
- [ ] Read entire plan from start to finish
- [ ] Identify one improvement opportunity
- [ ] Document the improvement
- [ ] Stop when no improvements found for 2 consecutive reads

### 4. Practical Validation
- [ ] Can each change be implemented with available tools?
- [ ] Will changes improve trading results or learning?
- [ ] Are changes testable and verifiable?

### 5. Document Review Process
- [ ] Note which iteration reached "no room for improvement"
- [ ] Document key decisions made during review
