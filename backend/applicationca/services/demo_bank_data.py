"""
Hardcoded demo data for bank reconciliation.
Source: HDFC Bank 74267 — Ambika Security Service, April 2024.

Bank statement convention:
  debit_amount  = money withdrawn / paid out of account
  credit_amount = money deposited / received into account

Tally ledger convention (mirrored):
  debit_amount  = "To" receipts — money INTO bank
  credit_amount = "By" payments — money OUT of bank

141 bank entries matched 1-to-1 with 141 ledger entries + 4 unreconciled bank-only entries.
"""

from datetime import date

DEMO_CONFIG = {
    "company": "Ambika Security Service",
    "bank_ledger": "HDFC Bank 74267",
    "period_from": date(2024, 4, 1),
    "period_to": date(2024, 4, 30),
    "opening_balance": 787104.87,
    "closing_balance": 454543.67,
}

# ---------------------------------------------------------------------------
# Bank statement transactions — abbreviated HDFC-style descriptions
# debit_amount  = Withdrawal; credit_amount = Deposit
# ---------------------------------------------------------------------------
DEMO_BANK_STMT = [
    # ── April 1 ──────────────────────────────────────────────────────────────
    {"date": date(2024, 4, 1),  "description": "NEFT-EXPERT FAC PRIVATE LIMITED-HDFC0001234",                "reference": "0000606008532321", "debit_amount": 340000.00,   "credit_amount": None},
    {"date": date(2024, 4, 1),  "description": "UPI-AGST PAID-YESB123456789-UPI",                            "reference": "0000606009189503", "debit_amount": 348144.00,   "credit_amount": None},
    {"date": date(2024, 4, 1),  "description": "POS 1234 CC PAYMENT HDFC",                                   "reference": "0000001495935187", "debit_amount": 11000.00,    "credit_amount": None},
    {"date": date(2024, 4, 1),  "description": "NEFT-SALARY HDFC0005555",                                    "reference": "0000001495998941", "debit_amount": 2500.00,     "credit_amount": None},
    # ── April 2 ──────────────────────────────────────────────────────────────
    {"date": date(2024, 4, 2),  "description": "NEFT-EPF PYMT EPFO-HDFC0002222",                             "reference": "0000606114075991", "debit_amount": 70476.00,    "credit_amount": None},
    {"date": date(2024, 4, 2),  "description": "NEFT-INW-IDEA INTL PVT-HDFC0003333",                         "reference": "0000606115837748", "credit_amount": 179231.00,  "debit_amount": None},
    # ── April 3 ──────────────────────────────────────────────────────────────
    {"date": date(2024, 4, 3),  "description": "CASH WITHDRAWL",                                             "reference": "0000606116058292", "debit_amount": 315.00,      "credit_amount": None},
    {"date": date(2024, 4, 3),  "description": "CASH WITHDRAWL",                                             "reference": "0000606129595150", "debit_amount": 315.00,      "credit_amount": None},
    {"date": date(2024, 4, 3),  "description": "CASH WITHDRAWL",                                             "reference": "0000606116859528", "debit_amount": 687.00,      "credit_amount": None},
    {"date": date(2024, 4, 3),  "description": "IMPS-INDUSLND BNK SAV-HDFC0004444",                          "reference": "0000606121117951", "debit_amount": 45000.00,    "credit_amount": None},
    {"date": date(2024, 4, 3),  "description": "POS 8899 CC PAYMENT",                                        "reference": "0000606227714194", "debit_amount": 9997.00,     "credit_amount": None},
    {"date": date(2024, 4, 3),  "description": "NEFT-SALARY HDFC0006666",                                    "reference": "0000606237118619", "debit_amount": 2000.00,     "credit_amount": None},
    # ── April 4 ──────────────────────────────────────────────────────────────
    {"date": date(2024, 4, 4),  "description": "IMPS-HDFC OD AC TRF-HDFC0007777",                            "reference": "0000606449003015", "debit_amount": 62125.00,    "credit_amount": None},
    {"date": date(2024, 4, 4),  "description": "NEFT-INW-SPRNG AKSHAYA-HDFC0008888",                         "reference": "0000606450970161", "credit_amount": 156069.00,  "debit_amount": None},
    # ── April 5 ──────────────────────────────────────────────────────────────
    {"date": date(2024, 4, 5),  "description": "NEFT-EPF PYMT EPFO-HDFC0009999",                             "reference": "0000606453288030", "debit_amount": 116657.00,   "credit_amount": None},
    {"date": date(2024, 4, 5),  "description": "NEFT-INW-PARAM RNWBL-HDFC0001111",                           "reference": "0000606458608759", "credit_amount": 2000000.00, "debit_amount": None},
    # ── April 6 ──────────────────────────────────────────────────────────────
    {"date": date(2024, 4, 6),  "description": "POS 6677 CC PAYMENT",                                        "reference": "0000606563390128", "debit_amount": 5471.18,     "credit_amount": None},
    {"date": date(2024, 4, 6),  "description": "NEFT-SALARY HDFC0002222",                                    "reference": "0000606565972728", "debit_amount": 5000.00,     "credit_amount": None},
    {"date": date(2024, 4, 6),  "description": "NEFT-INW-PARROGATE IND-HDFC0003333",                         "reference": "0000606569366097", "credit_amount": 29745.54,   "debit_amount": None},
    {"date": date(2024, 4, 6),  "description": "NEFT-EPF PYMT EPFO-HDFC0004444",                             "reference": "0000606569647848", "debit_amount": 408767.00,   "credit_amount": None},
    {"date": date(2024, 4, 6),  "description": "NEFT-SALARY HDFC0005555",                                    "reference": "0000606683301209", "debit_amount": 20000.00,    "credit_amount": None},
    {"date": date(2024, 4, 6),  "description": "NEFT-INW-PICKRNEW ENRGY-HDFC0006666",                        "reference": "0000606684729987", "credit_amount": 133280.00,  "debit_amount": None},
    {"date": date(2024, 4, 6),  "description": "NEFT-SALARY HDFC0007777",                                    "reference": "0000606800444612", "debit_amount": 1423776.50,  "credit_amount": None},
    {"date": date(2024, 4, 6),  "description": "NEFT-INW-SHIVAARNA TECH-HDFC0008888",                        "reference": "0000606802477147", "credit_amount": 558826.99,  "debit_amount": None},
    {"date": date(2024, 4, 6),  "description": "NEFT-INW-DUROFLEX PVT-HDFC0009999",                          "reference": "0000606807061189", "credit_amount": 84574.97,   "debit_amount": None},
    # ── April 7 ──────────────────────────────────────────────────────────────
    {"date": date(2024, 4, 7),  "description": "NEFT-HDFC CAR LOAN EMI-HDFC0001234",                         "reference": "0000607031135611", "debit_amount": 20459.00,    "credit_amount": None},
    # ── April 8 ──────────────────────────────────────────────────────────────
    {"date": date(2024, 4, 8),  "description": "NEFT-INW-ZEAL LIFE SCI-HDFC0002345",                         "reference": "0000607136895730", "credit_amount": 43067.00,   "debit_amount": None},
    {"date": date(2024, 4, 8),  "description": "NEFT-SALARY HDFC0003456",                                    "reference": "0000607138414730", "debit_amount": 440760.00,   "credit_amount": None},
    {"date": date(2024, 4, 8),  "description": "NEFT-INW-SMRITI COLLEGE-HDFC0004567",                        "reference": "0000607141172405", "credit_amount": 34800.00,   "debit_amount": None},
    {"date": date(2024, 4, 8),  "description": "UPI-STAFF WELFR@HDFC-UPI",                                   "reference": "0000607141934766", "debit_amount": 8392.12,     "credit_amount": None},
    # ── April 9 ──────────────────────────────────────────────────────────────
    {"date": date(2024, 4, 9),  "description": "NEFT-INW-MUNSHIRAM IND-HDFC0005678",                         "reference": "0000607144928100", "credit_amount": 28768.00,   "debit_amount": None},
    {"date": date(2024, 4, 9),  "description": "NEFT-INW-SHREE VARDHMN-HDFC0006789",                         "reference": "0000607251601039", "credit_amount": 188451.00,  "debit_amount": None},
    {"date": date(2024, 4, 9),  "description": "NEFT-INW-WORLD ONE LUX-HDFC0007890",                         "reference": "0000607254631529", "credit_amount": 203555.00,  "debit_amount": None},
    {"date": date(2024, 4, 9),  "description": "NEFT-INW-IDEA INTL PVT-HDFC0008901",                         "reference": "0000607360685103", "credit_amount": 873060.00,  "debit_amount": None},
    {"date": date(2024, 4, 9),  "description": "NEFT-SALARY HDFC0009012",                                    "reference": "0000607364058649", "debit_amount": 844213.00,   "credit_amount": None},
    {"date": date(2024, 4, 9),  "description": "NEFT-INW-IDEA INTL PVT-HDFC0000123",                         "reference": "0000607368983968", "credit_amount": 153813.00,  "debit_amount": None},
    {"date": date(2024, 4, 9),  "description": "UPI-STAFF WELFR@HDFC-UPI",                                   "reference": "0000607475340115", "debit_amount": 1724.00,     "credit_amount": None},
    {"date": date(2024, 4, 9),  "description": "UPI-OFFICE EXP@HDFC-UPI",                                    "reference": "0000607478443856", "debit_amount": 352.82,      "credit_amount": None},
    {"date": date(2024, 4, 9),  "description": "UPI-OFFICE EXP@HDFC-UPI",                                    "reference": "0000607583179786", "debit_amount": 352.82,      "credit_amount": None},
    # ── April 10 ─────────────────────────────────────────────────────────────
    {"date": date(2024, 4, 10), "description": "NEFT-INW-MANGLA ENGG LTD-HDFC0001234",                       "reference": "0000607583396974", "credit_amount": 128159.00,  "debit_amount": None},
    {"date": date(2024, 4, 10), "description": "NEFT-MAHINDRA CAR LOAN-HDFC0002345",                         "reference": "0000607585813453", "debit_amount": 33610.00,    "credit_amount": None},
    {"date": date(2024, 4, 10), "description": "NEFT-INW-MANGLA ENT FDY-HDFC0003456",                        "reference": "0000607588995234", "credit_amount": 34269.00,   "debit_amount": None},
    {"date": date(2024, 4, 10), "description": "NEFT-SALARY HDFC0004567",                                    "reference": "0000607694785819", "debit_amount": 973732.90,   "credit_amount": None},
    {"date": date(2024, 4, 10), "description": "NEFT-INW-PRESTIGE INST-HDFC0005678",                         "reference": "0000607695578139", "credit_amount": 74087.00,   "debit_amount": None},
    # ── April 11 ─────────────────────────────────────────────────────────────
    {"date": date(2024, 4, 11), "description": "NEFT-SALARY HDFC0006789",                                    "reference": "0000607699298844", "debit_amount": 415969.00,   "credit_amount": None},
    {"date": date(2024, 4, 11), "description": "NEFT-INW-SALARY REFUND-HDFC0007890",                         "reference": "0000607699886695", "credit_amount": 297.00,     "debit_amount": None},
    # ── April 12 ─────────────────────────────────────────────────────────────
    {"date": date(2024, 4, 12), "description": "NEFT-INW-EXPERT FAC MGT-HDFC0008901",                        "reference": "0000607706748422", "credit_amount": 22000.00,   "debit_amount": None},
    {"date": date(2024, 4, 12), "description": "NEFT-SALARY HDFC0009012",                                    "reference": "0000607708766897", "debit_amount": 2865.00,     "credit_amount": None},
    {"date": date(2024, 4, 12), "description": "UPI-RLNC JIO@JIONET-UPI",                                    "reference": "0000607711572812", "debit_amount": 2583.00,     "credit_amount": None},
    {"date": date(2024, 4, 12), "description": "NEFT-INW-GAJRA DIFF GRS-HDFC0001234",                        "reference": "0000607712363624", "credit_amount": 66640.00,   "debit_amount": None},
    {"date": date(2024, 4, 12), "description": "UPI-SUS@HDFC-UPI",                                           "reference": "0000607817672103", "debit_amount": 1832.00,     "credit_amount": None},
    {"date": date(2024, 4, 12), "description": "NEFT-SALARY HDFC0002345",                                    "reference": "0000607937104556", "debit_amount": 1575.00,     "credit_amount": None},
    # ── April 13 ─────────────────────────────────────────────────────────────
    {"date": date(2024, 4, 13), "description": "NEFT-INW-INDUSLND BNK-HDFC0003456",                          "reference": "0000606008532321", "credit_amount": 35000.00,   "debit_amount": None},
    {"date": date(2024, 4, 13), "description": "NEFT-INW-EXPERT FAC MGT-HDFC0004567",                        "reference": "0000606009189503", "credit_amount": 60000.00,   "debit_amount": None},
    {"date": date(2024, 4, 13), "description": "NEFT-SALARY HDFC0005678",                                    "reference": "0000001495935187", "debit_amount": 179844.02,   "credit_amount": None},
    # ── April 15 ─────────────────────────────────────────────────────────────
    {"date": date(2024, 4, 15), "description": "NEFT-MAHINDRA CAR LOAN-HDFC0006789",                         "reference": "0000001495998941", "debit_amount": 23670.00,    "credit_amount": None},
    {"date": date(2024, 4, 15), "description": "NEFT-MAHINDRA CAR LOAN-HDFC0007890",                         "reference": "0000606114075991", "debit_amount": 46840.00,    "credit_amount": None},
    # ── April 16 ─────────────────────────────────────────────────────────────
    {"date": date(2024, 4, 16), "description": "NEFT-INW-PRESTIGE INST-HDFC0008901",                         "reference": "0000606115837748", "credit_amount": 146511.00,  "debit_amount": None},
    {"date": date(2024, 4, 16), "description": "NEFT-INW-KESHAV IND PVT-HDFC0009012",                        "reference": "0000606116058292", "credit_amount": 79447.00,   "debit_amount": None},
    {"date": date(2024, 4, 16), "description": "NEFT-INW-SPRNG AKSHAYA-HDFC0001234",                         "reference": "0000606129595150", "credit_amount": 338251.00,  "debit_amount": None},
    {"date": date(2024, 4, 16), "description": "NEFT-SALARY HDFC0002345",                                    "reference": "0000606116859528", "debit_amount": 186638.00,   "credit_amount": None},
    {"date": date(2024, 4, 16), "description": "NEFT-INW-SPRNG AKSHAYA-HDFC0003456",                         "reference": "0000606121117951", "credit_amount": 153482.00,  "debit_amount": None},
    {"date": date(2024, 4, 16), "description": "NEFT-INW-SPRNG AKSHAYA-HDFC0004567",                         "reference": "0000606227714194", "credit_amount": 85534.00,   "debit_amount": None},
    {"date": date(2024, 4, 16), "description": "UPI-WELFR EXP@HDFC-UPI",                                     "reference": "0000606237118619", "debit_amount": 2500.00,     "credit_amount": None},
    # ── April 17 ─────────────────────────────────────────────────────────────
    {"date": date(2024, 4, 17), "description": "UPI-PETROL PUMP@IOCL-UPI",                                   "reference": "0000606449003015", "debit_amount": 4383.00,     "credit_amount": None},
    {"date": date(2024, 4, 17), "description": "NEFT-RENT ELEC PYMT-HDFC0005678",                            "reference": "0000606450970161", "debit_amount": 10000.00,    "credit_amount": None},
    {"date": date(2024, 4, 17), "description": "IMPS-INDUSLND BNK SAV-HDFC0006789",                          "reference": "0000606453288030", "debit_amount": 45000.00,    "credit_amount": None},
    {"date": date(2024, 4, 17), "description": "UPI-OFFICE EQUIP@HDFC-UPI",                                  "reference": "0000606458608759", "debit_amount": 1800.00,     "credit_amount": None},
    {"date": date(2024, 4, 17), "description": "POS 5599 CC PAYMENT",                                        "reference": "0000606563390128", "debit_amount": 7097.00,     "credit_amount": None},
    {"date": date(2024, 4, 17), "description": "NEFT-SALARY HDFC0007890",                                    "reference": "0000606565972728", "debit_amount": 376526.00,   "credit_amount": None},
    {"date": date(2024, 4, 17), "description": "UPI-TOUR TRVL@HDFC-UPI",                                     "reference": "0000606569366097", "debit_amount": 1500.00,     "credit_amount": None},
    {"date": date(2024, 4, 17), "description": "UPI-SUS@HDFC-UPI",                                           "reference": "0000606569647848", "debit_amount": 13500.00,    "credit_amount": None},
    # ── April 18 ─────────────────────────────────────────────────────────────
    {"date": date(2024, 4, 18), "description": "UPI-OFFICE EXP@HDFC-UPI",                                    "reference": "0000606683301209", "debit_amount": 100.00,      "credit_amount": None},
    {"date": date(2024, 4, 18), "description": "NEFT-INW-EXPERT FAC MGT-HDFC0008901",                        "reference": "0000606684729987", "credit_amount": 100000.00,  "debit_amount": None},
    {"date": date(2024, 4, 18), "description": "NEFT-BANK CHG-HDFC0009999",                                  "reference": "0000606800444612", "debit_amount": 5.90,        "credit_amount": None},
    {"date": date(2024, 4, 18), "description": "NEFT-INW-IDEA INTL PVT-HDFC0001234",                         "reference": "0000606802477147", "credit_amount": 102000.00,  "debit_amount": None},
    {"date": date(2024, 4, 18), "description": "CASH WITHDRAWL",                                             "reference": "0000606807061189", "debit_amount": 18875.00,    "credit_amount": None},
    {"date": date(2024, 4, 18), "description": "NEFT-SALARY HDFC0002345",                                    "reference": "0000607031135611", "debit_amount": 5000.00,     "credit_amount": None},
    {"date": date(2024, 4, 18), "description": "NEFT-EPF PYMT EPFO-HDFC0003456",                             "reference": "0000607136895730", "debit_amount": 165133.00,   "credit_amount": None},
    {"date": date(2024, 4, 18), "description": "NEFT-INW-AMR INDIA LTD-HDFC0004567",                         "reference": "0000607138414730", "credit_amount": 33660.00,   "debit_amount": None},
    {"date": date(2024, 4, 18), "description": "NEFT-INW-SHREE VARDHMN-HDFC0005678",                         "reference": "0000607141172405", "credit_amount": 169281.00,  "debit_amount": None},
    {"date": date(2024, 4, 18), "description": "IMPS-INDUSLND BNK SAV-HDFC0006789",                          "reference": "0000607141934766", "debit_amount": 65000.00,    "credit_amount": None},
    # ── April 19 ─────────────────────────────────────────────────────────────
    {"date": date(2024, 4, 19), "description": "UPI-TELE TV@HDFC-UPI",                                       "reference": "0000607144928100", "debit_amount": 17500.00,    "credit_amount": None},
    {"date": date(2024, 4, 19), "description": "NEFT-INW-PRESTIGE GLOB-HDFC0007890",                         "reference": "0000607251601039", "credit_amount": 86549.00,   "debit_amount": None},
    {"date": date(2024, 4, 19), "description": "UPI-TELE TV@HDFC-UPI",                                       "reference": "0000607254631529", "debit_amount": 470.82,      "credit_amount": None},
    {"date": date(2024, 4, 19), "description": "NEFT-SALARY HDFC0008901",                                    "reference": "0000607360685103", "debit_amount": 174074.00,   "credit_amount": None},
    {"date": date(2024, 4, 19), "description": "UPI-OTHER EXP@HDFC-UPI",                                     "reference": "0000607364058649", "debit_amount": 10.00,       "credit_amount": None},
    # ── April 20 ─────────────────────────────────────────────────────────────
    {"date": date(2024, 4, 20), "description": "NEFT-SALARY HDFC0009012",                                    "reference": "0000607368983968", "debit_amount": 27989.00,    "credit_amount": None},
    {"date": date(2024, 4, 20), "description": "IMPS-INDUSLND BNK SAV-HDFC0001234",                          "reference": "0000607475340115", "debit_amount": 40000.00,    "credit_amount": None},
    {"date": date(2024, 4, 20), "description": "NEFT-SALARY HDFC0002345",                                    "reference": "0000607478443856", "debit_amount": 3350.00,     "credit_amount": None},
    {"date": date(2024, 4, 20), "description": "UPI-TELE TV@HDFC-UPI",                                       "reference": "0000607583179786", "debit_amount": 6840.00,     "credit_amount": None},
    # ── April 22 ─────────────────────────────────────────────────────────────
    {"date": date(2024, 4, 22), "description": "NEFT-SALARY HDFC0003456",                                    "reference": "0000607583396974", "debit_amount": 84683.00,    "credit_amount": None},
    {"date": date(2024, 4, 22), "description": "UPI-SUS@HDFC-UPI",                                           "reference": "0000607585813453", "debit_amount": 3335.00,     "credit_amount": None},
    {"date": date(2024, 4, 22), "description": "NEFT-SALARY HDFC0004567",                                    "reference": "0000607588995234", "debit_amount": 30000.00,    "credit_amount": None},
    {"date": date(2024, 4, 22), "description": "NEFT-INW-PENTAGOAN LABS-HDFC0005678",                        "reference": "0000607694785819", "credit_amount": 72762.00,   "debit_amount": None},
    # ── April 23 ─────────────────────────────────────────────────────────────
    {"date": date(2024, 4, 23), "description": "NEFT-SALARY HDFC0006789",                                    "reference": "0000607695578139", "debit_amount": 5800.00,     "credit_amount": None},
    {"date": date(2024, 4, 23), "description": "UPI-STAFF WELFR@HDFC-UPI",                                   "reference": "0000607699298844", "debit_amount": 2000.00,     "credit_amount": None},
    {"date": date(2024, 4, 23), "description": "UPI-STAFF WELFR@HDFC-UPI",                                   "reference": "0000607699886695", "debit_amount": 3000.00,     "credit_amount": None},
    {"date": date(2024, 4, 23), "description": "POS 4455 CC PAYMENT",                                        "reference": "0000607706748422", "debit_amount": 5489.00,     "credit_amount": None},
    # ── April 24 ─────────────────────────────────────────────────────────────
    {"date": date(2024, 4, 24), "description": "NEFT-ADVANCE PYMT-HDFC0007890",                              "reference": "0000607708766897", "debit_amount": 10000.00,    "credit_amount": None},
    {"date": date(2024, 4, 24), "description": "NEFT-ADVANCE SALARY-HDFC0008901",                            "reference": "0000607711572812", "debit_amount": 15000.00,    "credit_amount": None},
    {"date": date(2024, 4, 24), "description": "CASH WITHDRAWL",                                             "reference": "0000607712363624", "debit_amount": 904.00,      "credit_amount": None},
    {"date": date(2024, 4, 24), "description": "CASH WITHDRAWL",                                             "reference": "0000607817672103", "debit_amount": 904.00,      "credit_amount": None},
    {"date": date(2024, 4, 24), "description": "UPI-PETROL@INDIANOIL-UPI",                                   "reference": "0000607937104556", "debit_amount": 5000.00,     "credit_amount": None},
    {"date": date(2024, 4, 24), "description": "NEFT-SALARY HDFC0009012",                                    "reference": "0000606008532321", "debit_amount": 22100.00,    "credit_amount": None},
    # ── April 25 ─────────────────────────────────────────────────────────────
    {"date": date(2024, 4, 25), "description": "CASH WITHDRAWL",                                             "reference": "0000606009189503", "debit_amount": 630.00,      "credit_amount": None},
    {"date": date(2024, 4, 25), "description": "NEFT-INW-EXPERT FAC MGT-HDFC0001234",                        "reference": "0000001495935187", "credit_amount": 32000.00,   "debit_amount": None},
    {"date": date(2024, 4, 25), "description": "NEFT-SALARY HDFC0002345",                                    "reference": "0000001495998941", "debit_amount": 31117.00,    "credit_amount": None},
    # ── April 26 ─────────────────────────────────────────────────────────────
    {"date": date(2024, 4, 26), "description": "NEFT-INW-PARAM RNWBL-HDFC0003456",                           "reference": "0000606114075991", "credit_amount": 2051921.00, "debit_amount": None},
    {"date": date(2024, 4, 26), "description": "NEFT-SALARY HDFC0004567",                                    "reference": "0000606115837748", "debit_amount": 6500.00,     "credit_amount": None},
    {"date": date(2024, 4, 26), "description": "NEFT-EPF PYMT EPFO-HDFC0005678",                             "reference": "0000606116058292", "debit_amount": 402015.00,   "credit_amount": None},
    {"date": date(2024, 4, 26), "description": "UPI-PRINT STATNY@HDFC-UPI",                                  "reference": "0000606129595150", "debit_amount": 50.00,       "credit_amount": None},
    {"date": date(2024, 4, 26), "description": "NEFT-INW-OTHER INCOME-HDFC0006789",                          "reference": "0000606116859528", "credit_amount": 758.98,     "debit_amount": None},
    # ── April 27 ─────────────────────────────────────────────────────────────
    {"date": date(2024, 4, 27), "description": "NEFT-SALARY HDFC0007890",                                    "reference": "0000606121117951", "debit_amount": 15089.00,    "credit_amount": None},
    {"date": date(2024, 4, 27), "description": "NEFT-SALARY HDFC0008901",                                    "reference": "0000606227714194", "debit_amount": 12991.00,    "credit_amount": None},
    {"date": date(2024, 4, 27), "description": "NEFT-SALARY HDFC0009012",                                    "reference": "0000606237118619", "debit_amount": 15793.00,    "credit_amount": None},
    {"date": date(2024, 4, 27), "description": "NEFT-SALARY HDFC0001234",                                    "reference": "0000606449003015", "debit_amount": 15793.00,    "credit_amount": None},
    {"date": date(2024, 4, 27), "description": "NEFT-SALARY HDFC0002345",                                    "reference": "0000606450970161", "debit_amount": 13682.00,    "credit_amount": None},
    {"date": date(2024, 4, 27), "description": "NEFT-SALARY HDFC0003456",                                    "reference": "0000606453288030", "debit_amount": 15089.00,    "credit_amount": None},
    {"date": date(2024, 4, 27), "description": "NEFT-SALARY HDFC0004567",                                    "reference": "0000606458608759", "debit_amount": 13682.00,    "credit_amount": None},
    {"date": date(2024, 4, 27), "description": "NEFT-SALARY HDFC0005678",                                    "reference": "0000606563390128", "debit_amount": 14385.00,    "credit_amount": None},
    {"date": date(2024, 4, 27), "description": "NEFT-SALARY HDFC0006789",                                    "reference": "0000606565972728", "debit_amount": 14385.00,    "credit_amount": None},
    {"date": date(2024, 4, 27), "description": "IMPS-INDUSLND BNK SAV-HDFC0007890",                          "reference": "0000606569366097", "debit_amount": 45000.00,    "credit_amount": None},
    {"date": date(2024, 4, 27), "description": "NEFT-INSUR PREM-HDFC0008901",                                "reference": "0000606569647848", "debit_amount": 17194.00,    "credit_amount": None},
    {"date": date(2024, 4, 27), "description": "NEFT-SALARY HDFC0009012",                                    "reference": "0000606683301209", "debit_amount": 62849.00,    "credit_amount": None},
    {"date": date(2024, 4, 27), "description": "POS 7780 CC PAYMENT",                                        "reference": "0000606684729987", "debit_amount": 3495.00,     "credit_amount": None},
    # ── April 29 ─────────────────────────────────────────────────────────────
    {"date": date(2024, 4, 29), "description": "NEFT-SALARY HDFC0001234",                                    "reference": "0000606800444612", "debit_amount": 1221551.00,  "credit_amount": None},
    {"date": date(2024, 4, 29), "description": "NEFT-SALARY HDFC0002345",                                    "reference": "0000606802477147", "debit_amount": 47379.00,    "credit_amount": None},
    {"date": date(2024, 4, 29), "description": "NEFT-INW-PARAM HERO PLT-HDFC0003456",                        "reference": "0000606807061189", "credit_amount": 900000.00,  "debit_amount": None},
    {"date": date(2024, 4, 29), "description": "IMPS-INDUSLND BNK SAV-HDFC0004567",                          "reference": "0000607031135611", "debit_amount": 40000.00,    "credit_amount": None},
    {"date": date(2024, 4, 29), "description": "NEFT-BANK CHG-HDFC0005678",                                  "reference": "0000607136895730", "debit_amount": 5.90,        "credit_amount": None},
    {"date": date(2024, 4, 29), "description": "NEFT-EPF PYMT EPFO-HDFC0006789",                             "reference": "0000607138414730", "debit_amount": 409343.00,   "credit_amount": None},
    {"date": date(2024, 4, 29), "description": "NEFT-BANK CHG-HDFC0007890",                                  "reference": "0000607141172405", "debit_amount": 5.90,        "credit_amount": None},
    {"date": date(2024, 4, 29), "description": "NEFT-BANK CHG-HDFC0008901",                                  "reference": "0000607141934766", "debit_amount": 5.90,        "credit_amount": None},
    {"date": date(2024, 4, 29), "description": "NEFT-BANK CHG-HDFC0009012",                                  "reference": "0000607144928100", "debit_amount": 5.90,        "credit_amount": None},
    {"date": date(2024, 4, 29), "description": "IMPS-HDFC OD AC TRF-HDFC0001234",                            "reference": "0000607251601039", "debit_amount": 100000.00,   "credit_amount": None},
    {"date": date(2024, 4, 29), "description": "NEFT-SALARY HDFC0002345",                                    "reference": "0000607254631529", "debit_amount": 20100.00,    "credit_amount": None},
    {"date": date(2024, 4, 29), "description": "NEFT-INW-TRI WALL PAK-HDFC0003456",                          "reference": "0000607360685103", "credit_amount": 36974.00,   "debit_amount": None},
    # ── April 30 ─────────────────────────────────────────────────────────────
    {"date": date(2024, 4, 30), "description": "UPI-SUS@HDFC-UPI",                                           "reference": "0000607364058649", "debit_amount": 10000.00,    "credit_amount": None},
    {"date": date(2024, 4, 30), "description": "UPI-SUS@HDFC-UPI",                                           "reference": "0000607368983968", "debit_amount": 3698.00,     "credit_amount": None},
    {"date": date(2024, 4, 30), "description": "UPI-SUS@HDFC-UPI",                                           "reference": "0000607475340115", "debit_amount": 25000.00,    "credit_amount": None},
    # ── Unreconciled bank entries (no matching Tally ledger entry) ─────────────
    {"date": date(2024, 4, 14), "description": "NEFT-INWARD MISC RCPT-HDFC0099887",                          "reference": "0000606014099887", "credit_amount": 18500.00,   "debit_amount": None},
    {"date": date(2024, 4, 21), "description": "UPI-PETROL@INDIANOIL-UPI",                                   "reference": "0000607021119900", "debit_amount": 6200.00,     "credit_amount": None},
    {"date": date(2024, 4, 28), "description": "POS 5599 PHARMACY APOLLO DELHI",                             "reference": "0000607028005599", "debit_amount": 2340.00,     "credit_amount": None},
    {"date": date(2024, 4, 30), "description": "ATM WHDFC MUMBAI",                                           "reference": "0000607030009999", "debit_amount": 10000.00,    "credit_amount": None},
]

# ---------------------------------------------------------------------------
# Tally ledger transactions — exact narrations as in Tally
# debit_amount  = "To" entries (receipts — money into bank)
# credit_amount = "By" entries (payments — money out of bank)
# ---------------------------------------------------------------------------
DEMO_LEDGER = [
    # ── April 1 ──────────────────────────────────────────────────────────────
    {"date": date(2024, 4, 1),  "narration": "Expert Facility Managment",                                    "credit_amount": 340000.00,   "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "2",  "guid": "demo-001"},
    {"date": date(2024, 4, 1),  "narration": "Gst Paid",                                                     "credit_amount": 348144.00,   "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "3",  "guid": "demo-002"},
    {"date": date(2024, 4, 1),  "narration": "CREDIT CARD",                                                  "credit_amount": 11000.00,    "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "4",  "guid": "demo-003"},
    {"date": date(2024, 4, 1),  "narration": "Salary A/c",                                                   "credit_amount": 2500.00,     "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "5",  "guid": "demo-004"},
    # ── April 2 ──────────────────────────────────────────────────────────────
    {"date": date(2024, 4, 2),  "narration": "EPF",                                                          "credit_amount": 70476.00,    "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "6",  "guid": "demo-005"},
    {"date": date(2024, 4, 2),  "narration": "Idea International Pvt. Ltd",                                  "debit_amount": 179231.00,    "credit_amount": None,      "voucher_type": "Receipt",  "voucher_number": "1",  "guid": "demo-006"},
    # ── April 3 ──────────────────────────────────────────────────────────────
    {"date": date(2024, 4, 3),  "narration": "Cash",                                                         "credit_amount": 315.00,      "debit_amount": None,       "voucher_type": "Contra",   "voucher_number": "1",  "guid": "demo-007"},
    {"date": date(2024, 4, 3),  "narration": "Cash",                                                         "credit_amount": 315.00,      "debit_amount": None,       "voucher_type": "Contra",   "voucher_number": "2",  "guid": "demo-008"},
    {"date": date(2024, 4, 3),  "narration": "Cash",                                                         "credit_amount": 687.00,      "debit_amount": None,       "voucher_type": "Contra",   "voucher_number": "3",  "guid": "demo-009"},
    {"date": date(2024, 4, 3),  "narration": "Induslnd Bank Saving A/c 9933",                                "credit_amount": 45000.00,    "debit_amount": None,       "voucher_type": "Contra",   "voucher_number": "11", "guid": "demo-010"},
    {"date": date(2024, 4, 3),  "narration": "CREDIT CARD",                                                  "credit_amount": 9997.00,     "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "8",  "guid": "demo-011"},
    {"date": date(2024, 4, 3),  "narration": "Salary A/c",                                                   "credit_amount": 2000.00,     "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "9",  "guid": "demo-012"},
    # ── April 4 ──────────────────────────────────────────────────────────────
    {"date": date(2024, 4, 4),  "narration": "HDFC Bank OD A/c",                                             "credit_amount": 62125.00,    "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "10", "guid": "demo-013"},
    {"date": date(2024, 4, 4),  "narration": "SPRNG AKSHAYA URJA PVT LTD",                                   "debit_amount": 156069.00,    "credit_amount": None,      "voucher_type": "Receipt",  "voucher_number": "2",  "guid": "demo-014"},
    # ── April 5 ──────────────────────────────────────────────────────────────
    {"date": date(2024, 4, 5),  "narration": "EPF",                                                          "credit_amount": 116657.00,   "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "11", "guid": "demo-015"},
    {"date": date(2024, 4, 5),  "narration": "Param Renewable Energy Pvt. Ltd.",                             "debit_amount": 2000000.00,   "credit_amount": None,      "voucher_type": "Receipt",  "voucher_number": "4",  "guid": "demo-016"},
    # ── April 6 ──────────────────────────────────────────────────────────────
    {"date": date(2024, 4, 6),  "narration": "CREDIT CARD",                                                  "credit_amount": 5471.18,     "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "12", "guid": "demo-017"},
    {"date": date(2024, 4, 6),  "narration": "Salary A/c",                                                   "credit_amount": 5000.00,     "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "13", "guid": "demo-018"},
    {"date": date(2024, 4, 6),  "narration": "PARROGATE INDUSTRIES PVT. LTD.",                               "debit_amount": 29745.54,     "credit_amount": None,      "voucher_type": "Receipt",  "voucher_number": "5",  "guid": "demo-019"},
    {"date": date(2024, 4, 6),  "narration": "EPF",                                                          "credit_amount": 408767.00,   "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "14", "guid": "demo-020"},
    {"date": date(2024, 4, 6),  "narration": "Salary A/c",                                                   "credit_amount": 20000.00,    "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "15", "guid": "demo-021"},
    {"date": date(2024, 4, 6),  "narration": "Pickrenew Energy Pvt. Ltd.",                                   "debit_amount": 133280.00,    "credit_amount": None,      "voucher_type": "Receipt",  "voucher_number": "6",  "guid": "demo-022"},
    {"date": date(2024, 4, 6),  "narration": "Salary A/c",                                                   "credit_amount": 1423776.50,  "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "16", "guid": "demo-023"},
    {"date": date(2024, 4, 6),  "narration": "Shivaarna Technofoams Pvt. Ltd.",                              "debit_amount": 558826.99,    "credit_amount": None,      "voucher_type": "Receipt",  "voucher_number": "9",  "guid": "demo-024"},
    {"date": date(2024, 4, 6),  "narration": "Duroflex Pvt. Ltd.",                                           "debit_amount": 84574.97,     "credit_amount": None,      "voucher_type": "Receipt",  "voucher_number": "10", "guid": "demo-025"},
    # ── April 7 ──────────────────────────────────────────────────────────────
    {"date": date(2024, 4, 7),  "narration": "HDFC Car loan 141004443",                                      "credit_amount": 20459.00,    "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "17", "guid": "demo-026"},
    # ── April 8 ──────────────────────────────────────────────────────────────
    {"date": date(2024, 4, 8),  "narration": "Zeal Life Sciences Pvt Ltd",                                   "debit_amount": 43067.00,     "credit_amount": None,      "voucher_type": "Receipt",  "voucher_number": "11", "guid": "demo-027"},
    {"date": date(2024, 4, 8),  "narration": "Salary A/c",                                                   "credit_amount": 440760.00,   "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "18", "guid": "demo-028"},
    {"date": date(2024, 4, 8),  "narration": "Smriti College of Pharmaceutical Education",                   "debit_amount": 34800.00,     "credit_amount": None,      "voucher_type": "Receipt",  "voucher_number": "12", "guid": "demo-029"},
    {"date": date(2024, 4, 8),  "narration": "Staff Welfare",                                                "credit_amount": 8392.12,     "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "19", "guid": "demo-030"},
    # ── April 9 ──────────────────────────────────────────────────────────────
    {"date": date(2024, 4, 9),  "narration": "Munshiram Industries",                                         "debit_amount": 28768.00,     "credit_amount": None,      "voucher_type": "Receipt",  "voucher_number": "13", "guid": "demo-031"},
    {"date": date(2024, 4, 9),  "narration": "Shree Vardhman Solvent Extraction",                            "debit_amount": 188451.00,    "credit_amount": None,      "voucher_type": "Receipt",  "voucher_number": "14", "guid": "demo-032"},
    {"date": date(2024, 4, 9),  "narration": "World One Luxury LLP",                                         "debit_amount": 203555.00,    "credit_amount": None,      "voucher_type": "Receipt",  "voucher_number": "15", "guid": "demo-033"},
    {"date": date(2024, 4, 9),  "narration": "Idea International Pvt. Ltd",                                  "debit_amount": 873060.00,    "credit_amount": None,      "voucher_type": "Receipt",  "voucher_number": "16", "guid": "demo-034"},
    {"date": date(2024, 4, 9),  "narration": "Salary A/c",                                                   "credit_amount": 844213.00,   "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "20", "guid": "demo-035"},
    {"date": date(2024, 4, 9),  "narration": "Idea International Pvt. Ltd",                                  "debit_amount": 153813.00,    "credit_amount": None,      "voucher_type": "Receipt",  "voucher_number": "17", "guid": "demo-036"},
    {"date": date(2024, 4, 9),  "narration": "Staff Welfare",                                                "credit_amount": 1724.00,     "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "21", "guid": "demo-037"},
    {"date": date(2024, 4, 9),  "narration": "Office Expenses",                                              "credit_amount": 352.82,      "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "22", "guid": "demo-038"},
    {"date": date(2024, 4, 9),  "narration": "Office Expenses",                                              "credit_amount": 352.82,      "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "23", "guid": "demo-039"},
    # ── April 10 ─────────────────────────────────────────────────────────────
    {"date": date(2024, 4, 10), "narration": "Mangla Engineering Ltd. Unit 1",                               "debit_amount": 128159.00,    "credit_amount": None,      "voucher_type": "Receipt",  "voucher_number": "18", "guid": "demo-040"},
    {"date": date(2024, 4, 10), "narration": "Mahinda Loan for XUV 700",                                     "credit_amount": 33610.00,    "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "24", "guid": "demo-041"},
    {"date": date(2024, 4, 10), "narration": "Mangla Enterprises Pvt.Ltd (Foundry Division)",                "debit_amount": 34269.00,     "credit_amount": None,      "voucher_type": "Receipt",  "voucher_number": "19", "guid": "demo-042"},
    {"date": date(2024, 4, 10), "narration": "Salary A/c",                                                   "credit_amount": 973732.90,   "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "25", "guid": "demo-043"},
    {"date": date(2024, 4, 10), "narration": "Prestige Institute of Management and Research",                "debit_amount": 74087.00,     "credit_amount": None,      "voucher_type": "Receipt",  "voucher_number": "21", "guid": "demo-044"},
    # ── April 11 ─────────────────────────────────────────────────────────────
    {"date": date(2024, 4, 11), "narration": "Salary A/c",                                                   "credit_amount": 415969.00,   "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "26", "guid": "demo-045"},
    {"date": date(2024, 4, 11), "narration": "Salary A/c",                                                   "debit_amount": 297.00,       "credit_amount": None,      "voucher_type": "Receipt",  "voucher_number": "22", "guid": "demo-046"},
    # ── April 12 ─────────────────────────────────────────────────────────────
    {"date": date(2024, 4, 12), "narration": "Expert Facility Managment",                                    "debit_amount": 22000.00,     "credit_amount": None,      "voucher_type": "Receipt",  "voucher_number": "23", "guid": "demo-047"},
    {"date": date(2024, 4, 12), "narration": "Salary A/c",                                                   "credit_amount": 2865.00,     "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "27", "guid": "demo-048"},
    {"date": date(2024, 4, 12), "narration": "Reliance Jio Infocom Ltd",                                     "credit_amount": 2583.00,     "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "28", "guid": "demo-049"},
    {"date": date(2024, 4, 12), "narration": "GAJRA DIFFERENTIAL GEARS LIMITED",                             "debit_amount": 66640.00,     "credit_amount": None,      "voucher_type": "Receipt",  "voucher_number": "24", "guid": "demo-050"},
    {"date": date(2024, 4, 12), "narration": "Sus",                                                          "credit_amount": 1832.00,     "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "29", "guid": "demo-051"},
    {"date": date(2024, 4, 12), "narration": "Salary A/c",                                                   "credit_amount": 1575.00,     "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "30", "guid": "demo-052"},
    # ── April 13 ─────────────────────────────────────────────────────────────
    {"date": date(2024, 4, 13), "narration": "Induslnd Bank Saving A/c 9933",                                "debit_amount": 35000.00,     "credit_amount": None,      "voucher_type": "Contra",   "voucher_number": "47", "guid": "demo-053"},
    {"date": date(2024, 4, 13), "narration": "Expert Facility Managment",                                    "debit_amount": 60000.00,     "credit_amount": None,      "voucher_type": "Receipt",  "voucher_number": "26", "guid": "demo-054"},
    {"date": date(2024, 4, 13), "narration": "Salary A/c",                                                   "credit_amount": 179844.02,   "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "31", "guid": "demo-055"},
    # ── April 15 ─────────────────────────────────────────────────────────────
    {"date": date(2024, 4, 15), "narration": "Mahindra Car Loan  3538",                                      "credit_amount": 23670.00,    "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "32", "guid": "demo-056"},
    {"date": date(2024, 4, 15), "narration": "Mahindra Car Loan Scorpio 9603",                               "credit_amount": 46840.00,    "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "33", "guid": "demo-057"},
    # ── April 16 ─────────────────────────────────────────────────────────────
    {"date": date(2024, 4, 16), "narration": "Prestige Institute of Management and Research",                "debit_amount": 146511.00,    "credit_amount": None,      "voucher_type": "Receipt",  "voucher_number": "28", "guid": "demo-058"},
    {"date": date(2024, 4, 16), "narration": "Keshav Industries (P) Ltd.",                                   "debit_amount": 79447.00,     "credit_amount": None,      "voucher_type": "Receipt",  "voucher_number": "29", "guid": "demo-059"},
    {"date": date(2024, 4, 16), "narration": "SPRNG AKSHAYA URJA PVT LTD",                                   "debit_amount": 338251.00,    "credit_amount": None,      "voucher_type": "Receipt",  "voucher_number": "30", "guid": "demo-060"},
    {"date": date(2024, 4, 16), "narration": "Salary A/c",                                                   "credit_amount": 186638.00,   "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "34", "guid": "demo-061"},
    {"date": date(2024, 4, 16), "narration": "SPRNG AKSHAYA URJA PVT LTD",                                   "debit_amount": 153482.00,    "credit_amount": None,      "voucher_type": "Receipt",  "voucher_number": "31", "guid": "demo-062"},
    {"date": date(2024, 4, 16), "narration": "SPRNG AKSHAYA URJA PVT LTD",                                   "debit_amount": 85534.00,     "credit_amount": None,      "voucher_type": "Receipt",  "voucher_number": "32", "guid": "demo-063"},
    {"date": date(2024, 4, 16), "narration": "Welfare Exp.",                                                  "credit_amount": 2500.00,     "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "35", "guid": "demo-064"},
    # ── April 17 ─────────────────────────────────────────────────────────────
    {"date": date(2024, 4, 17), "narration": "Petrol & Desel Exp.",                                          "credit_amount": 4383.00,     "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "36", "guid": "demo-065"},
    {"date": date(2024, 4, 17), "narration": "RENT AND ELECTRICITY",                                         "credit_amount": 10000.00,    "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "37", "guid": "demo-066"},
    {"date": date(2024, 4, 17), "narration": "Induslnd Bank Saving A/c 9933",                                "credit_amount": 45000.00,    "debit_amount": None,       "voucher_type": "Contra",   "voucher_number": "12", "guid": "demo-067"},
    {"date": date(2024, 4, 17), "narration": "Office Equipment",                                             "credit_amount": 1800.00,     "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "39", "guid": "demo-068"},
    {"date": date(2024, 4, 17), "narration": "CREDIT CARD",                                                  "credit_amount": 7097.00,     "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "40", "guid": "demo-069"},
    {"date": date(2024, 4, 17), "narration": "Salary A/c",                                                   "credit_amount": 376526.00,   "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "41", "guid": "demo-070"},
    {"date": date(2024, 4, 17), "narration": "Tour & Travelling Expenses",                                   "credit_amount": 1500.00,     "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "42", "guid": "demo-071"},
    {"date": date(2024, 4, 17), "narration": "Sus",                                                          "credit_amount": 13500.00,    "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "43", "guid": "demo-072"},
    # ── April 18 ─────────────────────────────────────────────────────────────
    {"date": date(2024, 4, 18), "narration": "Office Expenses",                                              "credit_amount": 100.00,      "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "44", "guid": "demo-073"},
    {"date": date(2024, 4, 18), "narration": "Expert Facility Managment",                                    "debit_amount": 100000.00,    "credit_amount": None,      "voucher_type": "Receipt",  "voucher_number": "33", "guid": "demo-074"},
    {"date": date(2024, 4, 18), "narration": "Bank Charge",                                                  "credit_amount": 5.90,        "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "45", "guid": "demo-075"},
    {"date": date(2024, 4, 18), "narration": "Idea International Pvt. Ltd",                                  "debit_amount": 102000.00,    "credit_amount": None,      "voucher_type": "Receipt",  "voucher_number": "34", "guid": "demo-076"},
    {"date": date(2024, 4, 18), "narration": "Cash",                                                         "credit_amount": 18875.00,    "debit_amount": None,       "voucher_type": "Contra",   "voucher_number": "4",  "guid": "demo-077"},
    {"date": date(2024, 4, 18), "narration": "Salary A/c",                                                   "credit_amount": 5000.00,     "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "46", "guid": "demo-078"},
    {"date": date(2024, 4, 18), "narration": "EPF",                                                          "credit_amount": 165133.00,   "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "47", "guid": "demo-079"},
    {"date": date(2024, 4, 18), "narration": "AMR INDIA LTD",                                                "debit_amount": 33660.00,     "credit_amount": None,      "voucher_type": "Receipt",  "voucher_number": "35", "guid": "demo-080"},
    {"date": date(2024, 4, 18), "narration": "Shree Vardhman Solvent Extraction",                            "debit_amount": 169281.00,    "credit_amount": None,      "voucher_type": "Receipt",  "voucher_number": "36", "guid": "demo-081"},
    {"date": date(2024, 4, 18), "narration": "Induslnd Bank Saving A/c 9933",                                "credit_amount": 65000.00,    "debit_amount": None,       "voucher_type": "Contra",   "voucher_number": "13", "guid": "demo-082"},
    # ── April 19 ─────────────────────────────────────────────────────────────
    {"date": date(2024, 4, 19), "narration": "TELEPHONE & TELEVISION EXP",                                   "credit_amount": 17500.00,    "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "49", "guid": "demo-083"},
    {"date": date(2024, 4, 19), "narration": "Prestige Institute Of Global Management, Indore",              "debit_amount": 86549.00,     "credit_amount": None,      "voucher_type": "Receipt",  "voucher_number": "37", "guid": "demo-084"},
    {"date": date(2024, 4, 19), "narration": "TELEPHONE & TELEVISION EXP",                                   "credit_amount": 470.82,      "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "50", "guid": "demo-085"},
    {"date": date(2024, 4, 19), "narration": "Salary A/c",                                                   "credit_amount": 174074.00,   "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "51", "guid": "demo-086"},
    {"date": date(2024, 4, 19), "narration": "Other Expenses",                                               "credit_amount": 10.00,       "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "52", "guid": "demo-087"},
    # ── April 20 ─────────────────────────────────────────────────────────────
    {"date": date(2024, 4, 20), "narration": "Salary A/c",                                                   "credit_amount": 27989.00,    "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "53", "guid": "demo-088"},
    {"date": date(2024, 4, 20), "narration": "Induslnd Bank Saving A/c 9933",                                "credit_amount": 40000.00,    "debit_amount": None,       "voucher_type": "Contra",   "voucher_number": "14", "guid": "demo-089"},
    {"date": date(2024, 4, 20), "narration": "Salary A/c",                                                   "credit_amount": 3350.00,     "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "55", "guid": "demo-090"},
    {"date": date(2024, 4, 20), "narration": "TELEPHONE & TELEVISION EXP",                                   "credit_amount": 6840.00,     "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "56", "guid": "demo-091"},
    # ── April 22 ─────────────────────────────────────────────────────────────
    {"date": date(2024, 4, 22), "narration": "Salary A/c",                                                   "credit_amount": 84683.00,    "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "57", "guid": "demo-092"},
    {"date": date(2024, 4, 22), "narration": "Sus",                                                          "credit_amount": 3335.00,     "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "58", "guid": "demo-093"},
    {"date": date(2024, 4, 22), "narration": "Salary A/c",                                                   "credit_amount": 30000.00,    "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "59", "guid": "demo-094"},
    {"date": date(2024, 4, 22), "narration": "Pentagoan Labs Ltd.",                                          "debit_amount": 72762.00,     "credit_amount": None,      "voucher_type": "Receipt",  "voucher_number": "38", "guid": "demo-095"},
    # ── April 23 ─────────────────────────────────────────────────────────────
    {"date": date(2024, 4, 23), "narration": "Salary A/c",                                                   "credit_amount": 5800.00,     "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "60", "guid": "demo-096"},
    {"date": date(2024, 4, 23), "narration": "Staff Welfare",                                                "credit_amount": 2000.00,     "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "61", "guid": "demo-097"},
    {"date": date(2024, 4, 23), "narration": "Staff Welfare",                                                "credit_amount": 3000.00,     "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "62", "guid": "demo-098"},
    {"date": date(2024, 4, 23), "narration": "CREDIT CARD",                                                  "credit_amount": 5489.00,     "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "63", "guid": "demo-099"},
    # ── April 24 ─────────────────────────────────────────────────────────────
    {"date": date(2024, 4, 24), "narration": "Advance for Site Construction",                                "credit_amount": 10000.00,    "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "64", "guid": "demo-100"},
    {"date": date(2024, 4, 24), "narration": "Advance Salary",                                               "credit_amount": 15000.00,    "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "65", "guid": "demo-101"},
    {"date": date(2024, 4, 24), "narration": "Cash",                                                         "credit_amount": 904.00,      "debit_amount": None,       "voucher_type": "Contra",   "voucher_number": "5",  "guid": "demo-102"},
    {"date": date(2024, 4, 24), "narration": "Cash",                                                         "credit_amount": 904.00,      "debit_amount": None,       "voucher_type": "Contra",   "voucher_number": "6",  "guid": "demo-103"},
    {"date": date(2024, 4, 24), "narration": "Petrol & Desel Exp.",                                          "credit_amount": 5000.00,     "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "66", "guid": "demo-104"},
    {"date": date(2024, 4, 24), "narration": "Salary A/c",                                                   "credit_amount": 22100.00,    "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "67", "guid": "demo-105"},
    # ── April 25 ─────────────────────────────────────────────────────────────
    {"date": date(2024, 4, 25), "narration": "Cash",                                                         "credit_amount": 630.00,      "debit_amount": None,       "voucher_type": "Contra",   "voucher_number": "7",  "guid": "demo-106"},
    {"date": date(2024, 4, 25), "narration": "Expert Facility Managment",                                    "debit_amount": 32000.00,     "credit_amount": None,      "voucher_type": "Receipt",  "voucher_number": "39", "guid": "demo-107"},
    {"date": date(2024, 4, 25), "narration": "Salary A/c",                                                   "credit_amount": 31117.00,    "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "68", "guid": "demo-108"},
    # ── April 26 ─────────────────────────────────────────────────────────────
    {"date": date(2024, 4, 26), "narration": "Param Renewable Energy Pvt. Ltd.",                             "debit_amount": 2051921.00,   "credit_amount": None,      "voucher_type": "Receipt",  "voucher_number": "40", "guid": "demo-109"},
    {"date": date(2024, 4, 26), "narration": "Salary A/c",                                                   "credit_amount": 6500.00,     "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "69", "guid": "demo-110"},
    {"date": date(2024, 4, 26), "narration": "EPF",                                                          "credit_amount": 402015.00,   "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "71", "guid": "demo-111"},
    {"date": date(2024, 4, 26), "narration": "Printing and Stationery Exp",                                  "credit_amount": 50.00,       "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "70", "guid": "demo-112"},
    {"date": date(2024, 4, 26), "narration": "Other Income",                                                 "debit_amount": 758.98,       "credit_amount": None,      "voucher_type": "Receipt",  "voucher_number": "41", "guid": "demo-113"},
    # ── April 27 ─────────────────────────────────────────────────────────────
    {"date": date(2024, 4, 27), "narration": "Salary A/c",                                                   "credit_amount": 15089.00,    "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "72", "guid": "demo-114"},
    {"date": date(2024, 4, 27), "narration": "Salary A/c",                                                   "credit_amount": 12991.00,    "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "73", "guid": "demo-115"},
    {"date": date(2024, 4, 27), "narration": "Salary A/c",                                                   "credit_amount": 15793.00,    "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "74", "guid": "demo-116"},
    {"date": date(2024, 4, 27), "narration": "Salary A/c",                                                   "credit_amount": 15793.00,    "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "75", "guid": "demo-117"},
    {"date": date(2024, 4, 27), "narration": "Salary A/c",                                                   "credit_amount": 13682.00,    "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "76", "guid": "demo-118"},
    {"date": date(2024, 4, 27), "narration": "Salary A/c",                                                   "credit_amount": 15089.00,    "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "77", "guid": "demo-119"},
    {"date": date(2024, 4, 27), "narration": "Salary A/c",                                                   "credit_amount": 13682.00,    "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "78", "guid": "demo-120"},
    {"date": date(2024, 4, 27), "narration": "Salary A/c",                                                   "credit_amount": 14385.00,    "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "79", "guid": "demo-121"},
    {"date": date(2024, 4, 27), "narration": "Salary A/c",                                                   "credit_amount": 14385.00,    "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "80", "guid": "demo-122"},
    {"date": date(2024, 4, 27), "narration": "Induslnd Bank Saving A/c 9933",                                "credit_amount": 45000.00,    "debit_amount": None,       "voucher_type": "Contra",   "voucher_number": "15", "guid": "demo-123"},
    {"date": date(2024, 4, 27), "narration": "Insurance Expences",                                           "credit_amount": 17194.00,    "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "83", "guid": "demo-124"},
    {"date": date(2024, 4, 27), "narration": "Salary A/c",                                                   "credit_amount": 62849.00,    "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "81", "guid": "demo-125"},
    {"date": date(2024, 4, 27), "narration": "CREDIT CARD",                                                  "credit_amount": 3495.00,     "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "84", "guid": "demo-126"},
    # ── April 29 ─────────────────────────────────────────────────────────────
    {"date": date(2024, 4, 29), "narration": "Salary A/c",                                                   "credit_amount": 1221551.00,  "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "85", "guid": "demo-127"},
    {"date": date(2024, 4, 29), "narration": "Salary A/c",                                                   "credit_amount": 47379.00,    "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "86", "guid": "demo-128"},
    {"date": date(2024, 4, 29), "narration": "Param Renewable Energy Pvt. Ltd. (Hero Plant)",                "debit_amount": 900000.00,    "credit_amount": None,      "voucher_type": "Receipt",  "voucher_number": "42", "guid": "demo-129"},
    {"date": date(2024, 4, 29), "narration": "Induslnd Bank Saving A/c 9933",                                "credit_amount": 40000.00,    "debit_amount": None,       "voucher_type": "Contra",   "voucher_number": "16", "guid": "demo-130"},
    {"date": date(2024, 4, 29), "narration": "Bank Charge",                                                  "credit_amount": 5.90,        "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "88", "guid": "demo-131"},
    {"date": date(2024, 4, 29), "narration": "EPF",                                                          "credit_amount": 409343.00,   "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "89", "guid": "demo-132"},
    {"date": date(2024, 4, 29), "narration": "Bank Charge",                                                  "credit_amount": 5.90,        "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "90", "guid": "demo-133"},
    {"date": date(2024, 4, 29), "narration": "Bank Charge",                                                  "credit_amount": 5.90,        "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "91", "guid": "demo-134"},
    {"date": date(2024, 4, 29), "narration": "Bank Charge",                                                  "credit_amount": 5.90,        "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "92", "guid": "demo-135"},
    {"date": date(2024, 4, 29), "narration": "HDFC Bank OD A/c",                                             "credit_amount": 100000.00,   "debit_amount": None,       "voucher_type": "Contra",   "voucher_number": "9",  "guid": "demo-136"},
    {"date": date(2024, 4, 29), "narration": "Salary A/c",                                                   "credit_amount": 20100.00,    "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "93", "guid": "demo-137"},
    {"date": date(2024, 4, 29), "narration": "TRI WALL PAK PVT. LTD.",                                       "debit_amount": 36974.00,     "credit_amount": None,      "voucher_type": "Receipt",  "voucher_number": "43", "guid": "demo-138"},
    # ── April 30 ─────────────────────────────────────────────────────────────
    {"date": date(2024, 4, 30), "narration": "Sus",                                                          "credit_amount": 10000.00,    "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "94", "guid": "demo-139"},
    {"date": date(2024, 4, 30), "narration": "Sus",                                                          "credit_amount": 3698.00,     "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "95", "guid": "demo-140"},
    {"date": date(2024, 4, 30), "narration": "Sus",                                                          "credit_amount": 25000.00,    "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "96", "guid": "demo-141"},
    # ── Unmatched ledger entries (Tally-only — amounts differ from any bank entry on same date) ──
    {"date": date(2024, 4, 14), "narration": "Other Income",                                                 "debit_amount": 18000.00,     "credit_amount": None,      "voucher_type": "Receipt",  "voucher_number": "27", "guid": "demo-142"},
    {"date": date(2024, 4, 21), "narration": "Petrol & Desel Exp.",                                          "credit_amount": 6500.00,     "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "54", "guid": "demo-143"},
    {"date": date(2024, 4, 28), "narration": "Staff Welfare",                                                "credit_amount": 2500.00,     "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "82", "guid": "demo-144"},
    {"date": date(2024, 4, 30), "narration": "Sus",                                                          "credit_amount": 9500.00,     "debit_amount": None,       "voucher_type": "Payment",  "voucher_number": "97", "guid": "demo-145"},
]
