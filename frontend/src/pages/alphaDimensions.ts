// The five A[i]LPHA dimensions — shared by the landing page and the live run.
// Sources reflect what each dimension actually reads in the backend
// (Form 4 insider data, FRED/BLS macro series, SEC filings, web + technical research).

export interface Dimension {
    letter: string;
    label: string;
    question: string;
    /** What this dimension reads, shown in the live run. */
    sources: string;
    sourceCount: number;
}

export const DIMENSIONS: Dimension[] = [
    {
        letter: 'A', label: 'Alignment with Stakeholders',
        question: 'Leadership behavior reveals truth. Assess management sentiment and shareholder alignment.',
        sources: 'Form 4 insider trades · MD&A sentiment', sourceCount: 4,
    },
    {
        letter: 'L', label: 'Liquidity & Macro Environment',
        question: 'Great companies falter in bad storms. Monitor regulatory headwinds and market tailwinds.',
        sources: 'FRED & BLS macro series', sourceCount: 3,
    },
    {
        letter: 'P', label: 'Performance & Fundamentals',
        question: 'The bedrock of analysis. Is growth organic? Are earnings quality signaling red flags?',
        sources: '10-K · 10-Q filings · earnings', sourceCount: 6,
    },
    {
        letter: 'H', label: 'Horizon & Competitive Moat',
        question: 'Long-term advantage matters. Distinguish trades from investments with structural durability.',
        sources: 'Filings · competitive web research', sourceCount: 5,
    },
    {
        letter: 'A', label: 'Action & Timing',
        question: 'Technical analysis reveals when. Find optimal entry zones and execution signals.',
        sources: 'Technical signals · web research', sourceCount: 3,
    },
];
