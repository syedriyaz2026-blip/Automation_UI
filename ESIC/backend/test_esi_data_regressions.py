from ESIC.backend.esi_data import validate_esi_records


def _valid_record():
    return {
        "Row_Num1": 47,
        "EmployeeNo": "ACE175557",
        "AssociateName": "Rachit Sharma",
        "DOB": "21-Mar-2003",
        "Gender": "M",
        "DOJ": "19-Oct-2026",
        "AadhaarNo": "254735054193",
        "AESICR_MobileNumber_of_Employee": "8979673368",
        "FatherName": "Ajay Sharma",
        "MaritalStatus": "S",
        "Presentaddress": "Present address",
        "Presentstate": "Delhi",
        "Presentdistrict": "New Delhi",
        "Presentpincode": "110001",
        "Permanentaddress": "Permanent address",
        "PermanentState": "Delhi",
        "PermanentDistrict": "New Delhi",
        "PermanentPincode": "110001",
        "NominieeName": "Nutan Sharma",
        "NominieeRelationShipWithID": "Mother",
        "NominieeAddress": "Nominee address",
        "NominieeState": "Delhi",
        "NominieeDistrict": "New Delhi",
        "NominieePincode": "110001",
        "AESUCR_Disp_State": "Delhi",
        "AESUCR_Disp_District": "New Delhi",
        "AESUCR_DispName": "Azadpur",
        "AESICR_Family_Disp_State": "Delhi",
        "AESICR_Family_Disp_District": "New Delhi",
        "AESICR_Family_DispName": "Azadpur",
        "AESICR_BankAccountNumber": "715310110011178",
        "AESICR_IFSC_Code": "BKID0007153",
        "Bank Account Type": "Savings",
        "Consent": "Yes",
    }


def test_invalid_source_row_number_does_not_leak_into_valid_rows():
    invalid = _valid_record()
    invalid["AadhaarNo"] = "not-an-aadhaar"
    result = validate_esi_records([invalid])
    assert result["invalid"] == 1
    assert result["valid_rows"] == []
