import React from 'react';
import { Link } from 'react-router-dom';
import './LibraryTitle.css';

/** Page title for the Documents area. On a sub-view it reads "Documents / My documents" and the parent is a link, as on the Repository page. */
export const LibraryTitle: React.FC<{ current?: string }> = ({ current }) => (
    <h1 className="docs-title">
        {current ? (
            <>
                <Link to="/documents" className="lib-crumb">Documents</Link>
                <span className="lib-crumb-sep" aria-hidden="true">/</span>
                <span aria-current="page">{current}</span>
            </>
        ) : 'Documents'}
    </h1>
);
