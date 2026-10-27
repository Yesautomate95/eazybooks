import React from 'react';
import LegalPageLayout from './LegalPageLayout';

const EndUserAgreement = () => (
  <LegalPageLayout title="End User License Agreement" updated="June 18, 2026">
    <p>
      This End User License Agreement (&ldquo;Agreement&rdquo;) governs your access to and use of
      EazyBooks, the AI-powered accounting and Tally automation platform operated by Toxic
      Technologies (&ldquo;Company&rdquo;). By creating an account or using the service, you agree to
      these terms.
    </p>

    <h2>1. License Grant</h2>
    <p>
      Subject to this Agreement, the Company grants you a limited, non-exclusive,
      non-transferable, revocable license to access and use EazyBooks (web app and desktop
      application) for your own internal accounting and bookkeeping purposes.
    </p>

    <h2>2. Your Responsibilities</h2>
    <ul>
      <li>You are responsible for the accuracy of documents and data you upload or connect.</li>
      <li>
        You must have the right to connect any third-party accounting system (Tally, QuickBooks,
        Zoho Books, Xero, SAP) and to authorize EazyBooks to read and write data on your behalf.
      </li>
      <li>You are responsible for safeguarding your account credentials.</li>
      <li>
        You will not use the service to upload unlawful content or to attempt to reverse-engineer,
        resell, or circumvent the platform&rsquo;s security.
      </li>
    </ul>

    <h2>3. AI-Generated Output</h2>
    <p>
      EazyBooks uses AI to extract and suggest ledger entries, classifications, and vouchers. AI
      output may contain errors. You are responsible for reviewing and approving all entries before
      they are posted to your accounting system &mdash; the Company is not liable for unreviewed
      automated postings.
    </p>

    <h2>4. Third-Party Integrations</h2>
    <p>
      Tally, QuickBooks, Zoho Books, Xero, and SAP are independent third-party products. EazyBooks
      is not affiliated with or endorsed by these vendors, and their availability, terms, and pricing
      are governed separately by those vendors.
    </p>

    <h2>5. Data Ownership</h2>
    <p>
      You retain ownership of your financial data. The Company will not use your data for any
      purpose other than providing and improving the service, as described in the{' '}
      <a href="/privacy-policy">Privacy Policy</a>.
    </p>

    <h2>6. Subscription &amp; Fees</h2>
    <p>
      Access to certain features may require a paid subscription. Fees, billing cycles, and
      cancellation terms will be presented at the time of purchase.
    </p>

    <h2>7. Termination</h2>
    <p>
      Either party may terminate this Agreement at any time. The Company may suspend or terminate
      access for violation of this Agreement, suspected fraud, or misuse of connected financial
      systems.
    </p>

    <h2>8. Disclaimer &amp; Limitation of Liability</h2>
    <p>
      The service is provided &ldquo;as is&rdquo; without warranties of any kind. To the maximum
      extent permitted by law, the Company is not liable for indirect, incidental, or consequential
      damages, including errors in financial postings resulting from unreviewed AI suggestions or
      third-party ERP downtime.
    </p>

    <h2>9. Governing Law</h2>
    <p>This Agreement is governed by the laws of India.</p>

    <h2>10. Changes</h2>
    <p>
      We may update this Agreement from time to time. Continued use of the service after changes
      take effect constitutes acceptance of the revised terms.
    </p>

    <h2>11. Contact</h2>
    <p>For questions about this Agreement, contact us through the in-app support channel.</p>
  </LegalPageLayout>
);

export default EndUserAgreement;
