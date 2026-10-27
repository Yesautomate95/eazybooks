import React from 'react';
import LegalPageLayout from './LegalPageLayout';

const PrivacyPolicy = () => (
  <LegalPageLayout title="Privacy Policy" updated="June 18, 2026">
    <p>
      Toxic Technologies (&ldquo;EazyBooks&rdquo;, &ldquo;we&rdquo;, &ldquo;us&rdquo;) provides an
      AI-powered platform for chartered accountants and finance teams, including document
      extraction, ledger matching, and integrations with Tally, QuickBooks, Zoho Books, Xero, and
      SAP. This policy explains what we collect, why, and how it is protected.
    </p>

    <h2>1. Information We Collect</h2>
    <ul>
      <li>
        <strong>Account data:</strong> name, email, and authentication identifiers from Google
        Sign-In or email/password sign-up.
      </li>
      <li>
        <strong>Financial documents and data:</strong> invoices, bank statements, ledgers, and
        accounting records you upload or sync from connected ERPs (Tally, QuickBooks, Zoho, Xero,
        SAP).
      </li>
      <li>
        <strong>Integration credentials:</strong> OAuth tokens and connection metadata required to
        read from and write to your connected accounting systems.
      </li>
      <li>
        <strong>Usage data:</strong> log data, device/browser information, and product analytics
        needed to operate and improve the service.
      </li>
    </ul>

    <h2>2. How We Use Your Information</h2>
    <ul>
      <li>To extract, classify, and reconcile financial documents using AI models.</li>
      <li>To push and sync vouchers, entries, and reports to your connected ERP.</li>
      <li>To authenticate you and maintain the security of your account.</li>
      <li>To provide support, diagnose issues, and improve product reliability.</li>
    </ul>

    <h2>3. AI Processing</h2>
    <p>
      Document text and structured financial data may be sent to third-party large language model
      providers (e.g. OpenAI) solely to perform extraction, classification, and matching. We do not
      permit these providers to use your data to train their models.
    </p>

    <h2>4. Data Storage &amp; Security</h2>
    <p>
      Financial data is stored in encrypted databases and object storage (including AWS S3) with
      access restricted to authorized systems and personnel. OAuth tokens for connected accounts are
      encrypted at rest. We use industry-standard transport encryption (TLS) for all data in transit.
    </p>

    <h2>5. Data Sharing</h2>
    <p>
      We do not sell your data. We share data only with: (a) the ERP/accounting platforms you
      explicitly connect, (b) infrastructure and AI processing subprocessors strictly to deliver the
      service, and (c) authorities where required by law.
    </p>

    <h2>6. Data Retention</h2>
    <p>
      We retain account and financial data for as long as your account is active, or as needed to
      provide the service. You may request deletion of your account and associated data, subject to
      statutory record-keeping requirements applicable to financial/accounting data.
    </p>

    <h2>7. Your Rights</h2>
    <p>
      You may access, correct, export, or request deletion of your personal data, and you may
      disconnect any connected ERP integration at any time from Settings.
    </p>

    <h2>8. Changes to This Policy</h2>
    <p>
      We may update this policy as the product evolves. Material changes will be notified in-app or
      by email.
    </p>

    <h2>9. Contact</h2>
    <p>For privacy questions or data requests, contact us through the in-app support channel.</p>
  </LegalPageLayout>
);

export default PrivacyPolicy;
