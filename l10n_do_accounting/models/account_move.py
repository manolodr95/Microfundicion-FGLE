import logging
from werkzeug import urls

from odoo import models, fields, api, _
from odoo.exceptions import ValidationError, UserError, AccessError

_logger = logging.getLogger(__name__)


class AccountMove(models.Model):
    _inherit = "account.move"

    def _get_l10n_do_cancellation_type(self):
        """ Return the list of cancellation types required by DGII. """
        return [
            ("01", _("01 - Pre-printed Invoice Impairment")),
            ("02", _("02 - Printing Errors (Pre-printed Invoice)")),
            ("03", _("03 - Defective Printing")),
            ("04", _("04 - Correction of Product Information")),
            ("05", _("05 - Product Change")),
            ("06", _("06 - Product Return")),
            ("07", _("07 - Product Omission")),
            ("08", _("08 - NCF Sequence Errors")),
            ("09", _("09 - For Cessation of Operations")),
            ("10", _("10 - Lossing or Hurting Of Counterfoil")),
        ]

    def _get_l10n_do_ecf_modification_code(self):
        """ Return the list of e-CF modification codes required by DGII. """
        return [
            ("1", _("01 - Total Cancellation")),
            ("2", _("02 - Text Correction")),
            ("3", _("03 - Amount correction")),
            ("4", _("04 - NCF replacement issued in contingency")),
            ("5", _("05 - Reference Electronic Consumer Invoice")),
        ]

    def _get_l10n_do_income_type(self):
        """ Return the list of income types required by DGII. """
        return [
            ("01", _("01 - Operational Incomes")),
            ("02", _("02 - Financial Incomes")),
            ("03", _("03 - Extraordinary Incomes")),
            ("04", _("04 - Leasing Incomes")),
            ("05", _("05 - Income for Selling Depreciable Assets")),
            ("06", _("06 - Other Incomes")),
        ]

    l10n_do_expense_type = fields.Selection(
        selection=lambda self: self.env["res.partner"]._get_l10n_do_expense_type(),
        string="Cost & Expense Type",
    )

    l10n_do_cancellation_type = fields.Selection(
        selection="_get_l10n_do_cancellation_type",
        string="Cancellation Type",
        copy=False,
    )

    l10n_do_income_type = fields.Selection(
        selection="_get_l10n_do_income_type",
        string="Income Type",
        copy=False,
        default=lambda self: self._context.get("l10n_do_income_type", "01"),
    )

    l10n_do_origin_ncf = fields.Char(
        string="Modifies",
    )

    ncf_expiration_date = fields.Date(
        string="Valid until",
        store=True,
    )
    is_debit_note = fields.Boolean()

    # DO NOT FORWARD PORT
    cancellation_type = fields.Selection(
        selection="_get_l10n_do_cancellation_type",
        string="Cancellation Type (deprecated)",
        copy=False,
    )
    is_ecf_invoice = fields.Boolean(
        copy=False,
        default=lambda self: self.env.user.company_id.l10n_do_ecf_issuer
        and self.env.user.company_id.l10n_do_country_code
        and self.env.user.company_id.l10n_do_country_code == "DO",
    )
    l10n_do_ecf_modification_code = fields.Selection(
        selection="_get_l10n_do_ecf_modification_code",
        string="e-CF Modification Code",
        copy=False,
        readonly=True,
        states={"draft": [("readonly", False)]},
    )
    l10n_do_ecf_security_code = fields.Char(string="e-CF Security Code", copy=False)
    l10n_do_ecf_sign_date = fields.Datetime(string="e-CF Sign Date", copy=False)
    l10n_do_electronic_stamp = fields.Char(
        string="Electronic Stamp",
        compute="_compute_l10n_do_electronic_stamp",
        store=True,
    )
    l10n_do_company_in_contingency = fields.Boolean(
        string="Company in contingency",
        compute="_compute_company_in_contingency",
    )

    @api.depends("company_id", "company_id.l10n_do_ecf_issuer")
    def _compute_company_in_contingency(self):
        for invoice in self:
            ecf_invoices = self.search([("is_ecf_invoice", "=", True)], limit=1)
            invoice.l10n_do_company_in_contingency = bool(
                ecf_invoices and not invoice.company_id.l10n_do_ecf_issuer
            )

    @api.depends("l10n_do_ecf_security_code", "l10n_do_ecf_sign_date", "invoice_date")
    @api.depends_context("l10n_do_ecf_service_env")
    def _compute_l10n_do_electronic_stamp(self):

        for invoice in self.filtered(
            lambda i: i.is_ecf_invoice
            and i.l10n_do_ecf_security_code
            and i.l10n_do_ecf_sign_date
        ):

            ecf_service_env = self.env.context.get("l10n_do_ecf_service_env", "CerteCF")
            doc_code_prefix = invoice.l10n_latam_document_type_id.doc_code_prefix
            has_sign_date = doc_code_prefix != "E32" or (
                doc_code_prefix == "E32" and invoice.amount_total_signed >= 250000
            )

            qr_string = "https://ecf.dgii.gov.do/%s/ConsultaTimbre?" % ecf_service_env
            qr_string += "RncEmisor=%s&" % invoice.company_id.vat or ""
            qr_string += (
                "RncComprador=%s&" % invoice.commercial_partner_id.vat
                if invoice.l10n_latam_document_type_id.doc_code_prefix[1:] != "43"
                else invoice.company_id.vat
            )
            qr_string += "ENCF=%s&" % invoice.ref or ""
            qr_string += "FechaEmision=%s&" % (
                invoice.invoice_date or fields.Date.today()
            ).strftime("%d-%m-%Y")
            qr_string += "MontoTotal=%s&" % ("%f" % abs(invoice.amount_total_signed)).rstrip(
                "0"
            ).rstrip(".")

            # DGII doesn't want FechaFirma if Consumo Electronico and < 250K
            # ¯\_(ツ)_/¯
            if has_sign_date:
                qr_string += (
                    "FechaFirma=%s&"
                    % fields.Datetime.context_timestamp(
                        self.with_context(tz="America/Santo_Domingo"),
                        invoice.l10n_do_ecf_sign_date,
                    ).strftime("%d-%m-%Y %H:%M:%S")
                )

            qr_string += "CodigoSeguridad=%s" % invoice.l10n_do_ecf_security_code or ""

            invoice.l10n_do_electronic_stamp = urls.url_quote_plus(qr_string)

    def button_cancel(self):

        fiscal_invoice = self.filtered(
            lambda inv: inv.l10n_latam_country_code == "DO"
            and self.type[-6:] in ("nvoice", "refund")
        )

        if len(fiscal_invoice) > 1:
            raise ValidationError(
                _("You cannot cancel multiple fiscal invoices at a time.")
            )

        if fiscal_invoice and not self.env.user.has_group(
            "l10n_do_accounting.group_l10n_do_fiscal_invoice_cancel"
        ):
            raise AccessError(_("You are not allowed to cancel Fiscal Invoices"))

        if fiscal_invoice:
            action = self.env.ref(
                "l10n_do_accounting.action_account_move_cancel"
            ).read()[0]
            action["context"] = {"default_move_id": fiscal_invoice.id}
            return action

        return super(AccountMove, self).button_cancel()

    def action_reverse(self):

        fiscal_invoice = self.filtered(
            lambda inv: inv.l10n_latam_country_code == "DO"
            and self.type[-6:] in ("nvoice", "refund")
        )
        if fiscal_invoice and not self.env.user.has_group(
            "l10n_do_accounting.group_l10n_do_fiscal_credit_note"
        ):
            raise AccessError(_("You are not allowed to issue Fiscal Credit Notes"))

        return super(AccountMove, self).action_reverse()

    @api.depends("ref")
    def _compute_l10n_latam_document_number(self):
        l10n_do_recs = self.filtered(lambda x: x.l10n_latam_country_code == "DO")
        for rec in l10n_do_recs:
            rec.l10n_latam_document_number = rec.ref
        remaining = self - l10n_do_recs
        remaining.l10n_latam_document_number = False
        super(AccountMove, remaining)._compute_l10n_latam_document_number()

    @api.onchange("l10n_latam_document_type_id", "l10n_latam_document_number")
    def _inverse_l10n_latam_document_number(self):
        for rec in self.filtered("l10n_latam_document_type_id"):
            if not rec.l10n_latam_document_number:
                rec.ref = ""
            else:
                document_type_id = rec.l10n_latam_document_type_id
                if document_type_id.l10n_do_ncf_type:
                    document_number = document_type_id._format_document_number(
                        rec.l10n_latam_document_number
                    )
                else:
                    document_number = rec.l10n_latam_document_number

                if rec.l10n_latam_document_number != document_number:
                    rec.l10n_latam_document_number = document_number
                rec.ref = document_number
        super(
            AccountMove, self.filtered(lambda m: m.l10n_latam_country_code != "DO")
        )._inverse_l10n_latam_document_number()

    def _get_l10n_latam_documents_domain(self):
        self.ensure_one()
        domain = super()._get_l10n_latam_documents_domain()
        if (
            self.journal_id.l10n_latam_use_documents
            and self.journal_id.company_id.country_id == self.env.ref("base.do")
        ):
            ncf_types = self.journal_id._get_journal_ncf_types(
                counterpart_partner=self.partner_id.commercial_partner_id, invoice=self
            )
            domain += [
                "|",
                ("l10n_do_ncf_type", "=", False),
                ("l10n_do_ncf_type", "in", ncf_types),
            ]
            codes = self.journal_id._get_journal_codes()
            if codes:
                domain.append(("code", "in", codes))
        return domain

    def _get_document_type_sequence(self):
        """ Return the match sequences for the given journal and invoice """
        self.ensure_one()
        if (
            self.journal_id.l10n_latam_use_documents
            and self.l10n_latam_country_code == "DO"
        ):
            res = self.journal_id.l10n_do_sequence_ids.filtered(
                lambda x: x.l10n_latam_document_type_id
                == self.l10n_latam_document_type_id
            )
            return res
        return super()._get_document_type_sequence()

    @api.constrains("type", "l10n_latam_document_type_id")
    def _check_invoice_type_document_type(self):
        super()._check_invoice_type_document_type()
        for rec in self.filtered(
            lambda r: r.company_id.country_id == self.env.ref("base.do")
            and r.l10n_latam_document_type_id
        ):
            partner_vat = rec.partner_id.vat
            l10n_latam_document_type = rec.l10n_latam_document_type_id
            if not partner_vat and l10n_latam_document_type.is_vat_required:
                raise ValidationError(
                    _(
                        "A VAT is mandatory for this type of NCF. "
                        "Please set the current VAT of this client"
                    )
                )

            elif rec.type in ("out_invoice", "out_refund"):
                if (
                    rec.amount_untaxed_signed >= 250000
                    and l10n_latam_document_type.l10n_do_ncf_type[-7:] != "special"
                    and not rec.partner_id.vat
                ):
                    raise UserError(
                        _(
                            "If the invoice amount is greater than RD$250,000.00 "
                            "the customer should have a VAT to validate the invoice"
                        )
                    )

    @api.constrains(
        "state", "line_ids", "l10n_latam_document_type_id", "company_id", "type"
    )
    def _check_special_exempt(self):
        """Validates that an invoice with a Special Tax Payer type does not contain
        nor ITBIS or ISC.
        See DGII Norma 05-19, Art 3 for further information.
        """
        for rec in self.filtered(
            lambda r: r.company_id.country_id == self.env.ref("base.do")
            and r.l10n_latam_document_type_id
            and r.type == "out_invoice"
            and r.state in ("draft", "cancel")
        ):
            if rec.l10n_latam_document_type_id.l10n_do_ncf_type[-7:] == "special":
                # If any invoice tax in ITBIS or ISC
                taxes = ("ITBIS", "ISC")
                if any(
                    [
                        tax
                        for tax in rec.line_ids.filtered("tax_line_id").filtered(
                            lambda tax: tax.tax_group_id.name in taxes
                            and tax.tax_base_amount != 0
                        )
                    ]
                ):
                    raise UserError(
                        _(
                            "You cannot validate and invoice of Fiscal Type "
                            "Regímen Especial with ITBIS/ISC.\n\n"
                            "See DGII General Norm 05-19, Art. 3 for further "
                            "information"
                        )
                    )

    @api.constrains("state", "company_id", "type", "amount_untaxed_signed")
    def _check_invoice_amount(self):
        """Validates that an invoices has an amount greater than 0."""
        for rec in self.filtered(
            lambda r: r.company_id.country_id == self.env.ref("base.do")
            and r.company_id
            and r.type == "out_invoice"
            and r.state != "draft"
        ):
            if rec.amount_untaxed_signed == 0:
                raise UserError(
                    _("You cannot validate an invoice with a total amount equals to 0.")
                )

    @api.constrains(
        "state",
        "line_ids",
        "partner_id",
        "company_id",
        "type",
        "l10n_latam_document_type_id",
    )
    def _check_products_export_ncf(self):
        """Validates that an invoices with a partner from country != DO
        and products type != service must have Exportaciones NCF.
        See DGII Norma 05-19, Art 10 for further information.
        """
        for rec in self.filtered(
            lambda r: r.company_id.country_id == self.env.ref("base.do")
            and r.l10n_latam_document_type_id
            and r.type == "out_invoice"
            and r.state in ("posted", "cancel")
        ):
            if rec.partner_id.country_id and rec.partner_id.country_id.code != "DO":
                if any(
                    [
                        p
                        for p in rec.invoice_line_ids.mapped("product_id")
                        if p.type != "service"
                    ]
                ):
                    if (
                        rec.l10n_latam_document_type_id.l10n_do_ncf_type[-6:]
                        != "export"
                    ):
                        raise UserError(
                            _(
                                "Goods sales to overseas customers must have "
                                "Exportaciones Fiscal Type"
                            )
                        )
                elif (
                    rec.l10n_latam_document_type_id.l10n_do_ncf_type[-8:] != "consumer"
                ):
                    raise UserError(
                        _(
                            "Services sales to overseas customer must have "
                            "Consumo Fiscal Type"
                        )
                    )

    @api.constrains(
        "state", "line_ids", "company_id", "l10n_latam_document_type_id", "type"
    )
    def _check_informal_withholding(self):
        """Validates an invoice with Comprobante de Compras has 100% ITBIS
        withholding.
        See DGII Norma 05-19, Art 7 for further information.
        """
        for rec in self.filtered(
            lambda r: r.company_id.country_id == self.env.ref("base.do")
            and r.l10n_latam_document_type_id
            and r.type == "in_invoice"
            and r.state == "draft"
        ):

            if rec.l10n_latam_document_type_id.l10n_do_ncf_type[-8:] == "informal":
                # If the sum of all taxes of category ITBIS is not 0
                if sum(
                    [
                        tax.amount
                        for tax in rec.line_ids.tax_ids.filtered(
                            lambda tax: tax.tax_group_id.name == "ITBIS"
                        )
                    ]
                ):
                    raise UserError(_("You must withhold 100% of ITBIS"))

    @api.onchange("l10n_latam_document_number", "l10n_do_origin_ncf")
    def _onchange_l10n_latam_document_number(self):
        for rec in self.filtered(
            lambda r: r.company_id.country_id == self.env.ref("base.do")
            and r.l10n_latam_document_type_id.l10n_do_ncf_type is not False
            and r.l10n_latam_document_number
        ):
            rec.l10n_latam_document_type_id._format_document_number(
                rec.l10n_latam_document_number
            )

    @api.onchange("partner_id")
    def _onchange_partner_id(self):
        if (
            self.company_id.country_id == self.env.ref("base.do")
            and self.l10n_latam_document_type_id
            and self.type == "in_invoice"
            and self.partner_id
        ):
            self.l10n_do_expense_type = (
                self.partner_id.l10n_do_expense_type
                if not self.l10n_do_expense_type
                else self.l10n_do_expense_type
            )

        return super(AccountMove, self)._onchange_partner_id()

    @api.constrains("name", "partner_id", "company_id")
    def _check_unique_vendor_number(self):
        for rec in self.filtered(
            lambda x: x.is_purchase_document()
            and x.company_id.country_id == self.env.ref("base.do")
            and x.l10n_latam_use_documents
            and x.l10n_latam_document_number
        ):
            pass
            # domain = [
            #     ('type', '=', rec.type),
            #     ('l10n_latam_document_number', '=', rec.l10n_latam_document_number),
            #     ('company_id', '=', rec.company_id.id),
            #     ('id', '!=', rec.id),
            #     ('commercial_partner_id', '=', rec.commercial_partner_id.id),
            # ]
            # if rec.search(domain):
            #     raise ValidationError(
            #         _(
            #             "NCF already used in another invoice\n\n"
            #             "The NCF *{}* has already been registered in another "
            #             "invoice with the same supplier. Look for it in "
            #             "invoices with canceled or draft states"
            #         ).format(rec.l10n_latam_document_number)
            #     )

    @api.constrains("state", "partner_id", "l10n_latam_document_number")
    def _check_fiscal_purchase(self):
        for rec in self.filtered(
            lambda r: r.company_id.country_id == self.env.ref("base.do")
            and r.l10n_latam_document_type_id.l10n_do_ncf_type is not False
            and r.type == "in_invoice"
            and r.l10n_latam_document_number
        ):
            l10n_latam_document_number = rec.l10n_latam_document_number
            l10n_latam_document_type = rec.l10n_latam_document_type_id.l10n_do_ncf_type

            if l10n_latam_document_number and l10n_latam_document_type[-6:] == "fiscal":
                if l10n_latam_document_number[1:3] in ("02", "32"):
                    raise ValidationError(
                        _(
                            "NCF *{}* does not correspond with the fiscal type\n\n"
                            "You cannot register Consumo NCF (02/32) for purchases"
                        ).format(l10n_latam_document_number)
                    )

                # try:
                #     from stdnum.do import ncf as ncf_validation
                #
                #     if len(
                #         l10n_latam_document_number
                #     ) == "11" and not ncf_validation.check_dgii(
                #         rec.partner_id.vat, l10n_latam_document_number
                #     ):
                #         raise ValidationError(
                #             _(
                #                 "NCF rejected by DGII\n\n"
                #                 "NCF *{}* of supplier *{}* was rejected by DGII's "
                #                 "validation service. Please validate if the NCF and "
                #                 "the supplier RNC are type correctly. Otherwhise "
                #                 "your supplier might not have this sequence approved "
                #                 "yet."
                #             ).format(l10n_latam_document_number, rec.partner_id.name)
                #         )
                #
                # except (ImportError, IOError) as err:
                #     _logger.debug(err)

    def _reverse_move_vals(self, default_values, cancel=True):

        ctx = self.env.context
        amount = ctx.get("amount")
        percentage = ctx.get("percentage")
        refund_type = ctx.get("refund_type")
        reason = ctx.get("reason")
        l10n_do_ecf_modification_code = ctx.get("l10n_do_ecf_modification_code")

        res = super(AccountMove, self)._reverse_move_vals(
            default_values=default_values, cancel=cancel
        )

        if self.l10n_latam_country_code == "DO":
            res["l10n_do_origin_ncf"] = self.l10n_latam_document_number
            res["l10n_do_ecf_modification_code"] = l10n_do_ecf_modification_code

        if refund_type in ("percentage", "fixed_amount"):
            price_unit = (
                amount
                if refund_type == "fixed_amount"
                else self.amount_untaxed * (percentage / 100)
            )
            res["line_ids"] = False
            res["invoice_line_ids"] = [
                (0, 0, {"name": reason or _("Refund"), "price_unit": price_unit})
            ]
        return res

    def post(self):

        res = super(AccountMove, self).post()

        non_payer_type_invoices = self.filtered(
            lambda inv: inv.company_id.country_id == self.env.ref("base.do")
            and inv.l10n_latam_use_documents
            and not inv.partner_id.l10n_do_dgii_tax_payer_type
        )
        if non_payer_type_invoices:
            raise ValidationError(_("Fiscal invoices require partner fiscal type"))

        return res

    def init(self):  # DO NOT FORWARD PORT
        cancelled_invoices = self.search(
            [
                ("state", "=", "cancel"),
                ("l10n_latam_use_documents", "=", True),
                ("cancellation_type", "!=", False),
                ("l10n_do_cancellation_type", "=", False),
            ]
        )
        for invoice in cancelled_invoices:
            invoice.l10n_do_cancellation_type = invoice.cancellation_type
