# Picoin Compute MVP

Product concept for Picoin Layer 2 utility.

## Short Answer

Picoin Compute is the first proposed utility platform on top of Picoin.

The user does not buy PI only to speculate or mine. The user stakes PI to access
a compute and AI task network. Workers, miners, and GPU providers execute useful
jobs and are paid by the Picoin economy, including the Science Compute Reserve.

The first version should not try to compete with ChatGPT, Gemini, Anthropic, AWS,
Render, or Akash. The first version should solve smaller, practical, repeatable
tasks that can be verified.

## Why This Exists

Today, most AI and cloud services require direct payment per request, per token,
or per hour. That model is expensive for small users and creates a dependency on
centralized providers.

Picoin Compute starts from a different idea:

- Users stake PI to receive access and usage quota.
- Workers provide compute and receive PI for verified work.
- The network reserve helps fund useful work instead of asking every user to pay
  directly per task.
- Validators check outputs, proofs, samples, or task receipts.
- PI becomes useful because it is required for staking, access, validation, and
  participation.

## What Is Already In Layer 1

Picoin Layer 1 already includes the base components needed for this model:

- mining;
- blocks and transactions;
- validators;
- staking;
- pools;
- wallets;
- full nodes;
- exchange integration;
- Science Compute Reserve allocation;
- Scientific Development Treasury allocation.

Current mainnet economics allocate part of each block to the Science Compute
Reserve. That reserve is the economic base that can later support rewards for
useful compute workers and GPU providers.

Layer 2 does not replace Layer 1. Layer 2 uses Layer 1 as settlement, access,
staking, accounting, and reward infrastructure.

## Platform Name

Working name:

```text
Picoin Compute
```

Possible public names:

- Picoin Compute
- Picoin AI Compute
- PiCompute
- Picoin WorkNet

Recommended initial name: `Picoin Compute`, because it is broader than AI and
does not overpromise a GPT competitor.

## What Problem It Solves

Picoin Compute should solve a simple problem:

Small users and businesses need affordable automation and compute tasks without
paying expensive subscriptions or cloud invoices for every action.

Examples:

- "I have a folder of invoices and need a structured CSV."
- "I have customer feedback and need categories and summaries."
- "I have a long PDF and need a clean report."
- "I have product files and need descriptions generated."
- "I need a batch of images resized, compressed, or converted."
- "I need a small open-source AI model to classify text."
- "I need repetitive data cleaning without hiring a developer."

The platform does not start as a general AI chatbot. It starts as a marketplace
for task templates that are easy to price, execute, and verify.

## The First User Experience

Minimum user flow:

1. User opens the Picoin Compute web app.
2. User connects or creates a Picoin wallet.
3. User stakes PI to unlock access.
4. User selects a task template.
5. User uploads a file or enters data.
6. The platform estimates compute cost and required quota.
7. The job is assigned to a worker node or GPU provider.
8. Worker executes the job.
9. Validators check the result or verify sampled proofs.
10. User downloads the result.
11. Worker is paid in PI.

The user experience should feel like a simple app, not like blockchain
infrastructure.

## Initial Task Categories

### 1. Document To Structured Data

Input:

- PDF invoice;
- CSV;
- TXT;
- image of a receipt;
- simple document batch.

Output:

- JSON;
- CSV;
- normalized table;
- summary report.

Problem solved:

Businesses spend time manually copying invoice, customer, product, or document
data. This task turns messy files into structured data.

Why it is a good MVP:

- clear input;
- clear output;
- useful for small businesses;
- easy to validate with schema checks and random sampling.

### 2. Data Cleaning And Report Generation

Input:

- CSV;
- Excel export;
- sales data;
- inventory data;
- survey results.

Output:

- cleaned CSV;
- charts;
- short report;
- anomaly list;
- summary by category.

Problem solved:

Many small businesses have raw data but no analyst. Picoin Compute can generate
basic reports from uploaded files.

Why it is a good MVP:

- common business need;
- can be done with CPU or modest GPU;
- results can be checked deterministically in many cases.

### 3. Batch Text Classification

Input:

- product reviews;
- support tickets;
- comments;
- leads;
- emails.

Output:

- category;
- sentiment;
- priority;
- summary;
- suggested action.

Problem solved:

Companies need to organize text at scale. This does not require building a new
ChatGPT. It can use open-source models or rule-assisted classification.

Why it is a good MVP:

- useful;
- repeatable;
- easy to price by item count;
- can be checked by sampling or validator agreement.

### 4. Content Operations

Input:

- product title;
- product facts;
- brand tone;
- image metadata.

Output:

- product descriptions;
- short summaries;
- SEO tags;
- marketplace listing drafts.

Problem solved:

Small merchants need content but do not want to pay a subscription for every
small task.

Why it is a good MVP:

- high demand;
- simple UX;
- results are easy for the user to inspect.

### 5. File Processing

Input:

- image folder;
- text files;
- CSV files;
- JSON files.

Output:

- compressed files;
- converted formats;
- resized images;
- merged files;
- normalized datasets.

Problem solved:

Users need boring batch processing that wastes time locally.

Why it is a good MVP:

- deterministic;
- easy to verify;
- does not require expensive AI in the first version.

## What Not To Build First

Do not start with:

- a ChatGPT competitor;
- a general LLM platform;
- a full AWS replacement;
- loans or banking;
- high-risk financial products;
- open-ended scientific promises.

Those can come later, but they are too broad for the first product.

The first product must be smaller:

```text
Stake PI, submit a useful task, receive a result, worker gets paid.
```

## How PI Enters The Model

PI is not only a payment coin in this model.

PI is used for:

- staking to access the platform;
- staking by workers to accept jobs;
- validator staking and reputation;
- reward accounting;
- priority or premium job routing later;
- governance over reserve spending policies;
- penalties when workers submit invalid results.

Initial access model:

```text
Stake PI -> receive monthly task quota -> submit jobs without direct per-task payment.
```

Worker model:

```text
Provide compute -> complete verified task -> receive PI from reserve/reward pool.
```

This creates utility because a user needs PI to access compute, and workers want
PI because they are paid in it.

## Why This Is Different

Most AI services require payment per request.

Picoin Compute can offer a staking-access model:

- user stakes PI instead of paying each task directly;
- the network reserve supports worker payments;
- workers earn PI for real compute;
- validators protect the quality of results;
- the same Layer 1 economy supports Layer 2 utility.

This makes Picoin more than a coin for payments. It becomes access to a compute
network.

## MVP Architecture

### Components

- Web app: task submission and wallet connection.
- Wallet: PI balance, staking, and task quota.
- Task coordinator: assigns jobs to workers.
- Worker client: executes task templates.
- Validator service: checks outputs and samples.
- Reserve accounting: tracks worker rewards.
- Result storage: stores outputs, receipts, and hashes.
- Layer 1 settlement: records staking, reward, and payment events.

### MVP Flow

```text
User stakes PI
User submits task
Task is queued
Worker accepts task
Worker returns output + receipt
Validator checks output
Result is delivered
Worker reward is recorded
User quota is reduced
```

## First MVP Scope

The first public MVP should support only a few task templates:

1. CSV cleaner and summary report.
2. PDF/text summarizer with structured output.
3. Batch text classification.
4. Image resize/compress/convert.
5. Product description generator using a small open-source model.

The goal is not to impress with a giant AI model. The goal is to prove that PI
can unlock useful compute.

## User Example

Example: small business owner.

Problem:

They have 300 customer reviews and want to know the top complaints.

Flow:

1. They stake PI.
2. They upload a CSV of reviews.
3. They choose "review classifier".
4. Picoin Compute assigns the task.
5. Workers classify the reviews.
6. Validators sample and check the output.
7. User receives:
   - top categories;
   - sentiment summary;
   - urgent complaints;
   - downloadable CSV.

Why PI matters:

They needed PI to stake and access the service. Workers earned PI for processing
the task.

## Worker Example

Example: GPU or server owner.

Problem:

They have unused compute capacity.

Flow:

1. They install the Picoin Compute worker.
2. They stake PI or register with reputation.
3. They receive tasks.
4. They process jobs.
5. They submit outputs and receipts.
6. Validators approve.
7. They earn PI.

Why PI matters:

The worker earns PI for useful compute, not only for base mining.

## Reserve Use

The Science Compute Reserve should not be spent randomly.

Initial reserve policy should be conservative:

- pay only for approved task categories;
- cap daily rewards;
- require valid receipts;
- require validator approval;
- track cost per task;
- publish transparent reserve reports;
- slash or ban abusive workers.

The reserve should bootstrap useful compute demand while the platform grows.

## Validation Strategy

Not all AI outputs are objectively verifiable. The MVP should prefer tasks with
clear validation.

Validation methods:

- schema validation;
- checksum and file hash;
- deterministic scripts;
- sample-based review;
- multiple worker agreement;
- validator scoring;
- user acceptance feedback;
- reputation history.

For the first version, avoid tasks where quality is completely subjective.

## Business Model

Initial model:

- users stake PI for access and quota;
- workers earn PI from verified jobs;
- the platform can take a small protocol fee later;
- premium users can stake more PI for higher quota or priority;
- businesses can stake larger amounts for team access.

Possible tiers:

```text
Starter stake: small monthly quota
Pro stake: higher quota and larger file limits
Business stake: team seats, API access, priority queue
Worker stake: eligibility to process paid tasks
Validator stake: eligibility to validate task outputs
```

## Roadmap

### Phase 1: MVP

- wallet login;
- staking check;
- one or two task templates;
- centralized result storage;
- worker CLI;
- manual or semi-automatic validation;
- PI reward accounting simulation.

### Phase 2: Networked Workers

- multiple workers;
- job assignment;
- validator sampling;
- reserve payout rules;
- public task receipts.

### Phase 3: Marketplace

- users submit jobs;
- workers choose categories;
- validators score quality;
- reserve reports;
- staking tiers.

### Phase 4: AI And GPU Expansion

- open-source model inference;
- GPU worker profiles;
- larger batch tasks;
- business API;
- integration with external apps.

## Investor Summary

Picoin Compute is the first practical utility direction for Picoin.

It does not claim to beat ChatGPT or AWS at launch. It starts with useful,
verifiable tasks that small users and businesses need.

The unique idea is:

```text
PI staking gives access to compute.
The reserve and network economy pay workers.
Validators protect quality.
Layer 1 settles staking, rewards, and trust.
```

That gives Picoin a clearer utility path:

- users need PI to access compute;
- workers earn PI for useful work;
- validators stake PI to validate;
- the reserve funds early compute supply;
- Layer 2 creates demand on top of Layer 1.

This is the first realistic product direction:

```text
Picoin Compute: stake PI, run useful tasks, reward real compute.
```
