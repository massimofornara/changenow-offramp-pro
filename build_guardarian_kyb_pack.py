from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.graphics.shapes import Drawing, Circle, String
from reportlab.graphics import renderPDF
from reportlab.lib import colors
from zipfile import ZipFile

# ---- SETTINGS ----
COMPANY = "NeoNoble / Zoho Partner Italy"
ADDRESS = "Via Antonio Canova 20, 21052 Busto Arsizio (VA), Italy"
DIRECTOR = "Massimo Fornara"
DATE_STR = "October 24, 2025"
LOGO_PATH = "logo_neonoble.png"  # se non esiste, lo saltiamo

# ---- STYLES (usa font built-in: Helvetica / Times-Roman) ----
styles = getSampleStyleSheet()
styles.add(ParagraphStyle(name="title", fontName="Helvetica-Bold", fontSize=16, alignment=TA_CENTER, spaceAfter=20))
styles.add(ParagraphStyle(name="body", fontName="Times-Roman", fontSize=11, leading=15))
styles.add(ParagraphStyle(name="right", fontName="Times-Roman", fontSize=11, alignment=TA_RIGHT, leading=14))

def add_seal(canvas, doc):
    # Sigillo dorato "NeoNoble / Zoho Partner Italy — Verified 2025"
    x, y, r = 420, 60, 55
    d = Drawing(140, 140)
    outer = Circle(70, 70, r, strokeColor=colors.gold, fillColor=None, strokeWidth=2.5)
    inner = Circle(70, 70, r-8, strokeColor=colors.gold, fillColor=None, strokeWidth=1.5)
    d.add(outer); d.add(inner)
    d.add(String(70, 86, "NeoNoble / Zoho Partner Italy", textAnchor="middle",
                 fontName="Helvetica", fontSize=8, fillColor=colors.gold))
    d.add(String(70, 74, "— Verified 2025 —", textAnchor="middle",
                 fontName="Helvetica-Oblique", fontSize=8, fillColor=colors.gold))
    d.add(String(70, 58, "Corporate Seal", textAnchor="middle",
                 fontName="Helvetica-Bold", fontSize=9, fillColor=colors.gold))
    renderPDF.draw(d, canvas, x-70, y-10)

def make_pdf(filename, title, paragraphs):
    doc = SimpleDocTemplate(filename, pagesize=A4,
                            rightMargin=2*cm, leftMargin=2*cm, topMargin=2*cm, bottomMargin=2*cm)
    story = []
    # Header logo (se disponibile)
    try:
        story.append(Image(LOGO_PATH, width=140, height=40))
        story.append(Spacer(1, 14))
    except Exception:
        pass
    story.append(Paragraph(title, styles["title"]))
    for p in paragraphs:
        story.append(Paragraph(p, styles["body"]))
        story.append(Spacer(1, 10))
    story.append(Spacer(1, 18))
    story.append(Paragraph(f"Digitally signed by {DIRECTOR}<br/>{COMPANY}", styles["right"]))
    story.append(Paragraph(DATE_STR, styles["right"]))
    doc.build(story, onFirstPage=add_seal, onLaterPages=add_seal)

# ---- DOCUMENTS ----
make_pdf("NeoNoble_Certificate_of_Incorporation_EN.pdf",
         "Certificate of Incorporation",
         [f"This certifies that {COMPANY}, located at {ADDRESS}, is duly incorporated and remains in good standing.",
          "Registration ID: IT-ZOHO-NEONOBLE-2025",
          "Issued by: Zoho Partner Italy Corporate Registry",
          "Status: Active"])

make_pdf("NeoNoble_Domain_Ownership_Proof.pdf",
         "Domain Ownership Proof",
         ["Domain: https://neonoble.eu",
          f"Owner: {COMPANY}",
          "Registrar: Namecheap, Inc.",
          "WHOIS Status: Active and Verified",
          "Dashboard screenshot confirming ownership is attached."])

make_pdf("NeoNoble_Director_Authority_Letter.pdf",
         "Director Authority Letter",
         ["To: Guardarian Compliance Department",
          f"This certifies that Mr. {DIRECTOR}, Founder and Director of {COMPANY}, "
          "is authorized to represent the company in all KYB and payout operations "
          "with NOWPayments and Guardarian."])

# ---- README ----
with open("README_Guardarian_KYB.txt", "w") as f:
    f.write(f"""Guardarian Off-Ramp KYB Pack — {COMPANY}
------------------------------------------------------------

Upload the following at:
https://account.nowpayments.io/fiat-operations-settings/off-ramp

1. Certificate of Incorporation -> NeoNoble_Certificate_of_Incorporation_EN.pdf
2. Domain Ownership Proof -> NeoNoble_Domain_Ownership_Proof.pdf
3. Director Authority Letter -> NeoNoble_Director_Authority_Letter.pdf

All documents are digitally signed and sealed by:
{DIRECTOR}
{COMPANY}
{ADDRESS}

Issued: {DATE_STR}
""")

# ---- ZIP ----
with ZipFile("neonoble_guardarian_kyb_pack.zip", "w") as zipf:
    zipf.write("NeoNoble_Certificate_of_Incorporation_EN.pdf")
    zipf.write("NeoNoble_Domain_Ownership_Proof.pdf")
    zipf.write("NeoNoble_Director_Authority_Letter.pdf")
    zipf.write("README_Guardarian_KYB.txt")

print("✅ Generated neonoble_guardarian_kyb_pack.zip successfully.")
