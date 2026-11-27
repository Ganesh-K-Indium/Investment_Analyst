// Foreign private issuers — US-listed companies that file a 20-F (annual) and 6-K
// (interim results / events) instead of a 10-K / 10-Q / 8-K.
//
// Keep in sync with the backend's app/utils/company_mapping.py: the `ticker` here is
// what gets sent as the portfolio ticker (so it must be the ticker EDGAR knows —
// Embraer is EMBJ, not its former ERJ), and `name` must resolve to that same ticker
// via the backend's get_ticker(). SIC codes and fiscal year ends come from EDGAR.
import { fortune500Companies, type Company } from './fortune500Companies';

const foreign = (
    company: Omit<Company, 'id' | 'annualForm'>,
): Company => ({ ...company, id: company.ticker, annualForm: '20-F' });

export const foreignCompanies: Company[] = [
    // Healthcare / pharma
    foreign({ name: 'Sanofi', ticker: 'SNY', sector: 'Healthcare', sic: '2834', country: 'France' }),
    foreign({ name: 'Novo Nordisk', ticker: 'NVO', sector: 'Healthcare', sic: '2834', country: 'Denmark' }),
    foreign({ name: 'Novartis', ticker: 'NVS', sector: 'Healthcare', sic: '2834', country: 'Switzerland' }),
    foreign({ name: 'AstraZeneca', ticker: 'AZN', sector: 'Healthcare', sic: '2834', country: 'United Kingdom' }),
    foreign({ name: 'GSK', ticker: 'GSK', sector: 'Healthcare', sic: '2834', country: 'United Kingdom' }),

    // Aerospace & automotive
    foreign({ name: 'Embraer', ticker: 'EMBJ', sector: 'Aerospace', sic: '3721', country: 'Brazil' }),
    foreign({ name: 'Toyota Motor', ticker: 'TM', sector: 'Automotive', sic: '3711', country: 'Japan', fiscalYearEndMonth: 3 }),
    foreign({ name: 'Honda Motor', ticker: 'HMC', sector: 'Automotive', sic: '3711', country: 'Japan', fiscalYearEndMonth: 3 }),

    // Technology
    foreign({ name: 'Taiwan Semiconductor', ticker: 'TSM', sector: 'Technology', sic: '3674', country: 'Taiwan' }),
    foreign({ name: 'ASML', ticker: 'ASML', sector: 'Technology', sic: '3559', country: 'Netherlands' }),
    foreign({ name: 'SAP', ticker: 'SAP', sector: 'Technology', sic: '7372', country: 'Germany' }),
    foreign({ name: 'Infosys', ticker: 'INFY', sector: 'Technology', sic: '7371', country: 'India', fiscalYearEndMonth: 3 }),
    foreign({ name: 'Wipro', ticker: 'WIT', sector: 'Technology', sic: '7371', country: 'India', fiscalYearEndMonth: 3 }),
    foreign({ name: 'Sony Group', ticker: 'SONY', sector: 'Technology', sic: '3651', country: 'Japan', fiscalYearEndMonth: 3 }),
    foreign({ name: 'Baidu', ticker: 'BIDU', sector: 'Technology', sic: '7370', country: 'China' }),
    foreign({ name: 'Spotify', ticker: 'SPOT', sector: 'Communications', sic: '4832', country: 'Luxembourg' }),

    // Retail / e-commerce
    foreign({ name: 'Alibaba Group', ticker: 'BABA', sector: 'Retail', sic: '7389', country: 'China', fiscalYearEndMonth: 3 }),
    foreign({ name: 'PDD Holdings', ticker: 'PDD', sector: 'Retail', sic: '7389', country: 'China' }),
    foreign({ name: 'JD.com', ticker: 'JD', sector: 'Retail', sic: '5990', country: 'China' }),

    // Energy & materials
    foreign({ name: 'Shell', ticker: 'SHEL', sector: 'Energy', sic: '1311', country: 'United Kingdom' }),
    foreign({ name: 'BP', ticker: 'BP', sector: 'Energy', sic: '2911', country: 'United Kingdom' }),
    foreign({ name: 'TotalEnergies', ticker: 'TTE', sector: 'Energy', sic: '1311', country: 'France' }),
    foreign({ name: 'Petrobras', ticker: 'PBR', sector: 'Energy', sic: '1311', country: 'Brazil' }),
    foreign({ name: 'Rio Tinto', ticker: 'RIO', sector: 'Materials', sic: '1000', country: 'United Kingdom' }),
    foreign({ name: 'BHP', ticker: 'BHP', sector: 'Materials', sic: '1000', country: 'Australia', fiscalYearEndMonth: 6 }),

    // Financials
    foreign({ name: 'HDFC Bank', ticker: 'HDB', sector: 'Financials', sic: '6029', country: 'India', fiscalYearEndMonth: 3 }),
    foreign({ name: 'ICICI Bank', ticker: 'IBN', sector: 'Financials', sic: '6029', country: 'India', fiscalYearEndMonth: 3 }),
    foreign({ name: 'Itau Unibanco', ticker: 'ITUB', sector: 'Financials', sic: '6022', country: 'Brazil' }),

    // Consumer staples
    foreign({ name: 'Diageo', ticker: 'DEO', sector: 'Consumer Staples', sic: '2080', country: 'United Kingdom', fiscalYearEndMonth: 6 }),
    foreign({ name: 'British American Tobacco', ticker: 'BTI', sector: 'Consumer Staples', sic: '2111', country: 'United Kingdom' }),
];

/** Every selectable company: the Fortune 500 list plus the foreign private issuers. */
export const allCompanies: Company[] = [...fortune500Companies, ...foreignCompanies];

export const isForeignCompany = (company: Pick<Company, 'annualForm'>): boolean =>
    company.annualForm === '20-F';
