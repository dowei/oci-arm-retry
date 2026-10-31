import oci
import time
import datetime

# ╔══════════════════════════════════════════════════════╗
# ║  Target : VM.Standard.A1.Flex (ARM)                  ║
# ║  Spec   : 2 OCPU / 12 GB RAM / 100 GB disk (default) ║
# ║  Arch   : ARM (Ampere)                               ║
# ║  Tier   : Oracle Always Free                         ║
# ║  OS     : Canonical Ubuntu 22.04                     ║
# ╚══════════════════════════════════════════════════════╝

# ─── Configuration ───────────────────────────────────────
# Compartment to create the instance in. Leave empty to use the tenancy (root) compartment
# from your OCI config / the OCI_TENANCY secret; a compartment OCID also works here.
# NOTE: a *user* OCID (ocid1.user...) does NOT work - it fails with 404 NotAuthorizedOrNotFound.
COMPARTMENT_ID = ""  # ocid1.compartment.oc1..xxxxxx (optional)
# OCID of an existing subnet to launch into (this script creates no network resources).
# OCI Console -> Networking -> Virtual Cloud Networks -> <your VCN> -> Subnets -> Copy OCID
SUBNET_ID = "ocid1.subnet.oc1.ap-tokyo-1.aaaaaaaaahgcfvji5tvhtyve3jrkxuazkq5ngmbtef5w5ev5p3ytjgmyiokq"
SSH_PUBLIC_KEY = "ssh-rsa AAAAB3NzaC1yc2EAAAABJQAAAQEAkvfwKeEOP1T+7gVehfqKpaMUkuUY3gX3yK6HrJL1X2XQcV5A1pbRXKbQVQquKiYDVdmBhq7UyoIiDEGqa7oeeh6erz1/UlQ1ukt9p4KxoEzG6dx6cVcGEXgIF3QuoHqm98t/5Tb2tKHEER8L5op/7YvdUC0SvTpOoBFoC0JY/LG43G6iIIhN3s4EEK3vKmfP6/DW+C0AweU5WSl+4GEILgIjQfCyGMC2q/gXdLdmIOvc1aHfdTthnMf5r80FEOfKmAND8V6S0DhgSUUbOZ6XhlMVVYAa+idej55co+9nlh1gcEnCVOxxdlS9nPMo8MSq7Yy/KIh3RZhA6x8FOZ2dHQ== rsa-key-20200410"  # Replace with your SSH public key (.pub file content)
INSTANCE_NAME = "streamlit-server"
ARM_OCPUS = 2
ARM_MEMORY_IN_GBS = 12
BOOT_VOLUME_SIZE_IN_GBS = 100
RETRY_INTERVAL = 90  # seconds
# ────────────────────────────────────────────────────────

# ─── OCI Authentication ──────────────────────────────────
# [Local] Create a config file at ~/.oci/config
#   See README.md for format; key_file should point to your API private key .pem
#
# [GitHub Actions] No config file needed
#   The workflow auto-creates it from GitHub Secrets — see README.md
# ────────────────────────────────────────────────────────
config = oci.config.from_file()

if not COMPARTMENT_ID:
    COMPARTMENT_ID = config["tenancy"]
if not COMPARTMENT_ID.startswith(("ocid1.tenancy.", "ocid1.compartment.")):
    raise SystemExit(
        "COMPARTMENT_ID must be a tenancy (ocid1.tenancy...) or compartment (ocid1.compartment...) "
        f"OCID, got: {COMPARTMENT_ID}"
    )


def get_availability_domain():
    identity = oci.identity.IdentityClient(config)
    ads = identity.list_availability_domains(COMPARTMENT_ID).data
    return ads[0].name


def get_ubuntu_arm_image():
    compute = oci.core.ComputeClient(config)
    images = compute.list_images(
        COMPARTMENT_ID,
        operating_system="Canonical Ubuntu",
        operating_system_version="22.04",
        shape="VM.Standard.A1.Flex",
        sort_by="TIMECREATED",
        sort_order="DESC",
    ).data
    if not images:
        raise Exception("Ubuntu 22.04 ARM image not found")
    return images[0].id


def try_create_instance(subnet_id, ad_name, image_id):
    compute = oci.core.ComputeClient(config)
    instance = compute.launch_instance(
        oci.core.models.LaunchInstanceDetails(
            compartment_id=COMPARTMENT_ID,
            display_name=INSTANCE_NAME,
            availability_domain=ad_name,
            shape="VM.Standard.A1.Flex",
            shape_config=oci.core.models.LaunchInstanceShapeConfigDetails(
                ocpus=ARM_OCPUS,
                memory_in_gbs=ARM_MEMORY_IN_GBS,
            ),
            source_details=oci.core.models.InstanceSourceViaImageDetails(
                image_id=image_id,
                boot_volume_size_in_gbs=BOOT_VOLUME_SIZE_IN_GBS,
            ),
            create_vnic_details=oci.core.models.CreateVnicDetails(
                subnet_id=subnet_id,
                assign_public_ip=True,
            ),
            metadata={"ssh_authorized_keys": SSH_PUBLIC_KEY},
        )
    ).data
    return instance


def main():
    print(f"Using existing subnet: {SUBNET_ID}")
    subnet_id = SUBNET_ID

    # A subnet OCID carries its region: ocid1.subnet.oc1.<region>.<unique>
    subnet_region = SUBNET_ID.split(".")[3] if SUBNET_ID.count(".") >= 4 else None
    if subnet_region and subnet_region != config["region"]:
        print(
            f"⚠️  OCI config region is '{config['region']}' but the subnet is in "
            f"'{subnet_region}' - the launch will not find it"
        )

    print("Fetching availability domain...")
    ad_name = get_availability_domain()
    print(f"AD: {ad_name}")

    print("Fetching Ubuntu 22.04 ARM image...")
    image_id = get_ubuntu_arm_image()
    print(f"Image ID: {image_id}")

    attempt = 0
    while True:
        attempt += 1
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"\n[{now}] Attempt #{attempt} to create instance...")

        try:
            instance = try_create_instance(subnet_id, ad_name, image_id)
            print(f"\n✅ Success! Instance created")
            print(f"   ID: {instance.id}")
            print(f"   State: {instance.lifecycle_state}")
            print(f"   Check Oracle Cloud Console for the public IP")
            break
        except oci.exceptions.ServiceError as e:
            if "Out of host capacity" in str(e) or "capacity" in str(e).lower():
                print(f"❌ Out of capacity, retrying...")
            else:
                print(f"❌ API error: {e.message}, retrying...")
        except Exception as e:
            print(f"⚠️ Network timeout or other error, retrying... ({type(e).__name__})")

        time.sleep(RETRY_INTERVAL)


if __name__ == "__main__":
    main()
