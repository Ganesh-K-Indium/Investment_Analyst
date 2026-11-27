import React, { useState, useRef, useEffect, useLayoutEffect } from 'react';
import { createPortal } from 'react-dom';
import type { Company } from '../../data/fortune500Companies';
import { isForeignCompany } from '../../data/foreignCompanies';
import './CompanySelect.css';

interface CompanySelectProps {
    companies: Company[];
    selectedCompanies: Company[];
    onChange: (companies: Company[]) => void;
    placeholder?: string;
}

type Segment = 'all' | 'us' | 'foreign';

const SEGMENTS: { key: Segment; label: string }[] = [
    { key: 'all', label: 'All' },
    { key: 'us', label: 'US' },
    { key: 'foreign', label: 'Foreign' },
];

export const CompanySelect: React.FC<CompanySelectProps> = ({
    companies,
    selectedCompanies,
    onChange,
    placeholder = 'Search and select companies...'
}) => {
    const [isOpen, setIsOpen] = useState(false);
    const [searchTerm, setSearchTerm] = useState('');
    const [segment, setSegment] = useState<Segment>('all');
    const [dropdownPos, setDropdownPos] = useState<{ top?: number; bottom?: number; left: number; width: number } | null>(null);
    const containerRef = useRef<HTMLDivElement>(null);
    const dropdownRef = useRef<HTMLDivElement>(null);
    const inputRef = useRef<HTMLInputElement>(null);

    // Close dropdown when clicking outside (the dropdown is portaled, so check both nodes)
    useEffect(() => {
        const handleClickOutside = (event: MouseEvent) => {
            const target = event.target as Node;
            if (containerRef.current?.contains(target) || dropdownRef.current?.contains(target)) return;
            setIsOpen(false);
        };
        document.addEventListener('mousedown', handleClickOutside);
        return () => document.removeEventListener('mousedown', handleClickOutside);
    }, []);

    // The dropdown is rendered in a portal with position: fixed so it is not clipped by
    // the modal's scroll container. Anchor it to the input box and flip above when there is
    // more room there than below.
    useLayoutEffect(() => {
        if (!isOpen) return;
        const updatePosition = () => {
            const rect = containerRef.current?.getBoundingClientRect();
            if (!rect) return;
            const spaceBelow = window.innerHeight - rect.bottom;
            const spaceAbove = rect.top;
            const flipUp = spaceBelow < 280 && spaceAbove > spaceBelow;
            // Wide enough to read company names and metadata, clamped inside the viewport
            const width = Math.min(Math.max(rect.width, 520), window.innerWidth - 16);
            const left = Math.max(8, Math.min(rect.left, window.innerWidth - width - 8));
            setDropdownPos({
                left,
                width,
                ...(flipUp
                    ? { bottom: window.innerHeight - rect.top + 4 }
                    : { top: rect.bottom + 4 }),
            });
        };
        updatePosition();
        // Capture scroll events so scrolling any ancestor (e.g. the modal body) repositions it
        window.addEventListener('scroll', updatePosition, true);
        window.addEventListener('resize', updatePosition);
        return () => {
            window.removeEventListener('scroll', updatePosition, true);
            window.removeEventListener('resize', updatePosition);
        };
    }, [isOpen]);

    // The US/Foreign filter only appears when the list actually contains foreign issuers.
    const hasForeign = companies.some(isForeignCompany);

    const filteredCompanies = companies.filter(company => {
        if (segment === 'foreign' && !isForeignCompany(company)) return false;
        if (segment === 'us' && isForeignCompany(company)) return false;
        const search = searchTerm.toLowerCase();
        return (
            company.name.toLowerCase().includes(search) ||
            company.ticker.toLowerCase().includes(search) ||
            company.sector.toLowerCase().includes(search) ||
            (company.sic ?? '').includes(search) ||
            (company.country ?? '').toLowerCase().includes(search)
        );
    });

    const isSelected = (company: Company) =>
        selectedCompanies.some(c => c.id === company.id);

    const handleSelect = (company: Company) => {
        if (isSelected(company)) {
            onChange(selectedCompanies.filter(c => c.id !== company.id));
        } else {
            onChange([...selectedCompanies, company]);
        }
    };

    const handleRemove = (companyId: string) => {
        onChange(selectedCompanies.filter(c => c.id !== companyId));
    };

    return (
        <div className="company-select" ref={containerRef}>
            {/* Selected Companies Tags */}
            <div
                className="company-select__input-container"
                onClick={() => {
                    setIsOpen(true);
                    inputRef.current?.focus();
                }}
            >
                {selectedCompanies.map(company => (
                    <span key={company.id} className="company-select__tag">
                        <span className="company-select__tag-ticker">{company.ticker}</span>
                        <span className="company-select__tag-name">{company.name}</span>
                        <button
                            type="button"
                            className="company-select__tag-remove"
                            onClick={(e) => {
                                e.stopPropagation();
                                handleRemove(company.id);
                            }}
                        >
                            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                <line x1="18" y1="6" x2="6" y2="18" />
                                <line x1="6" y1="6" x2="18" y2="18" />
                            </svg>
                        </button>
                    </span>
                ))}
                <input
                    ref={inputRef}
                    type="text"
                    className="company-select__search"
                    placeholder={selectedCompanies.length === 0 ? placeholder : ''}
                    value={searchTerm}
                    onChange={(e) => setSearchTerm(e.target.value)}
                    onFocus={() => setIsOpen(true)}
                />
            </div>

            {/* Dropdown (portaled to body so it floats above modals and is not clipped) */}
            {isOpen && dropdownPos && createPortal(
                <div
                    ref={dropdownRef}
                    className="company-select__dropdown"
                    style={{ top: dropdownPos.top, bottom: dropdownPos.bottom, left: dropdownPos.left, width: dropdownPos.width }}
                >
                    {hasForeign && (
                        <div className="company-select__segments" role="tablist">
                            {SEGMENTS.map(({ key, label }) => (
                                <button
                                    key={key}
                                    type="button"
                                    role="tab"
                                    aria-selected={segment === key}
                                    className={`company-select__segment ${segment === key ? 'company-select__segment--active' : ''}`}
                                    // mousedown would blur the search box and fire the outside-click handler
                                    onMouseDown={(e) => e.preventDefault()}
                                    onClick={() => setSegment(key)}
                                >
                                    {label}
                                </button>
                            ))}
                        </div>
                    )}
                    {filteredCompanies.length === 0 ? (
                        <div className="company-select__empty">
                            No companies found matching "{searchTerm}"
                        </div>
                    ) : (
                        filteredCompanies.map(company => (
                            <div
                                key={company.id}
                                className={`company-select__option ${isSelected(company) ? 'company-select__option--selected' : ''}`}
                                onClick={() => handleSelect(company)}
                            >
                                <div className="company-select__option-info">
                                    <span className="company-select__option-ticker">{company.ticker}</span>
                                    <span className="company-select__option-name">{company.name}</span>
                                    {isForeignCompany(company) && (
                                        <span className="company-select__option-badge" title={`Foreign private issuer — files ${company.annualForm}`}>
                                            {company.annualForm}
                                        </span>
                                    )}
                                </div>
                                {company.country && <span className="company-select__option-country">{company.country}</span>}
                                {company.sic && <span className="company-select__option-sic" title="SIC code">SIC {company.sic}</span>}
                                <span className="company-select__option-sector">{company.sector}</span>
                                {isSelected(company) && (
                                    <svg className="company-select__check" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                        <polyline points="20 6 9 17 4 12" />
                                    </svg>
                                )}
                            </div>
                        ))
                    )}
                </div>,
                document.body
            )}
        </div>
    );
};

export default CompanySelect;
