import logging
import xml.etree.ElementTree as ET
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)

class Form4Parser:
    """
    Parses SEC Form 4 XML content into structured data.
    """
    
    def parse_xml(self, xml_content: str) -> Optional[Dict[str, Any]]:
        try:
            root = ET.fromstring(xml_content)
            
            # 1. Issuer Info
            issuer = root.find('issuer')
            if issuer is None: return None
            
            issuer_symbol = self._get_text(issuer, 'issuerTradingSymbol')
            issuer_name = self._get_text(issuer, 'issuerName')
            
            # Period of Report (filing date)
            period_of_report = self._get_text(root, 'periodOfReport')
            
            # 2. Reporting Owner Info (Can be multiple)
            # We will take the first one as primary for now.
            owners = root.findall('reportingOwner')
            if not owners: return None
            
            primary_owner = owners[0]
            id_data = primary_owner.find('reportingOwnerId')
            rpt_owner_name = self._get_text(id_data, 'rptOwnerName')
            
            rel = primary_owner.find('reportingOwnerRelationship')
            officer_title = self._get_text(rel, 'officerTitle')
            is_director = self._get_text(rel, 'isDirector') == '1'
            is_officer = self._get_text(rel, 'isOfficer') == '1'
            is_ten_percent_owner = self._get_text(rel, 'isTenPercentOwner') == '1'

            transactions = []

            # 4. Non-Derivative Transactions (Table 1)
            non_deriv = root.find('nonDerivativeTable')
            if non_deriv is not None:
                for trans in non_deriv.findall('nonDerivativeTransaction'):
                    t_data = self._parse_transaction(trans, is_derivative=False)
                    if t_data:
                        transactions.append(t_data)
                        
            # 5. Derivative Transactions (Table 2)
            deriv = root.find('derivativeTable')
            if deriv is not None:
                for trans in deriv.findall('derivativeTransaction'):
                    t_data = self._parse_transaction(trans, is_derivative=True)
                    if t_data:
                        transactions.append(t_data)

            return {
                'issuer_symbol': issuer_symbol,
                'issuer_name': issuer_name,
                'period_of_report': period_of_report,
                'rpt_owner_name': rpt_owner_name,
                'rpt_owner_title': officer_title,
                'is_director': is_director,
                'is_officer': is_officer,
                'is_ten_percent_owner': is_ten_percent_owner,
                'transactions': transactions
            }
            
        except ET.ParseError as e:
            logger.error(f"XML Parse Error: {e}")
            return None
        except Exception as e:
            logger.error(f"Error parsing XML: {e}")
            return None

    def _parse_transaction(self, node, is_derivative: bool) -> Dict[str, Any]:
        """Helper to parse common transaction fields."""
        t_data = {'is_derivative': is_derivative}
        
        # Security Title
        t_data['security_title'] = self._get_text(node, 'securityTitle/value')

        # Date
        t_data['date'] = self._get_text(node, 'transactionDate/value')
        
        # Coding
        coding = node.find('transactionCoding')
        t_data['code'] = self._get_text(coding, 'transactionCode')
        
        # Amounts
        amounts = node.find('transactionAmounts')
        if amounts is not None:
            t_data['shares'] = self._safe_float(amounts, 'transactionShares/value')
            t_data['price'] = self._safe_float(amounts, 'transactionPricePerShare/value')
            t_data['acq_disp'] = self._get_text(amounts, 'transactionAcquiredDisposedCode/value')
        else:
            t_data['shares'] = 0.0
            t_data['price'] = 0.0
            t_data['acq_disp'] = None

        return t_data

    def _get_text(self, node, path: str) -> Optional[str]:
        if node is None: return None
        n = node.find(path)
        if n is not None and n.text:
            return n.text.strip()
        return None

    def _safe_float(self, node, path: str) -> float:
        text = self._get_text(node, path)
        if text:
            try:
                return float(text)
            except ValueError:
                return 0.0
        return 0.0
        
    def _get_bool(self, node, path: str) -> bool:
        text = self._get_text(node, path)
        if text:
            return text == '1' or text.lower() == 'true'
        return False
